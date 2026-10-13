// Tool page body: files column + options column with the sticky run button, then the job.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, createJob, type Job, type ToolDetail } from "../api/client";
import {
  acceptAttr,
  fileMatches,
  formatTitle,
  planTarget,
  targetsFromServer,
  type FormatIndex,
  type Target,
  type TargetPlan,
} from "../catalog";
import { Alert } from "../components/Alert";
import { FilePicker, type Item } from "../components/FilePicker";
import { Icon } from "../components/Icon";
import { JobView } from "../components/JobView";
import { PageGrid } from "../components/PageGrid";
import { toPlan, type Entry } from "../components/pagePlan";
import { TargetField } from "../components/TargetField";
import {
  AuxField,
  OptionsForm,
  buildOptions,
  fieldsOf,
  initialValues,
  validate,
  widgetField,
  type Errors,
  type JsonSchema,
  type Values,
} from "../components/OptionsForm";
import { useLimits } from "../limits";
import { errorTitle, formatBytes } from "../messages";

export function RunTool({ tool, formats }: { tool: ToolDetail; formats: FormatIndex }) {
  const fields = fieldsOf(tool.optionsSchema as JsonSchema);
  const grid = widgetField(tool.optionsSchema as JsonSchema, "page-grid");
  const [items, setItems] = useState<Item[]>([]);
  const [aux, setAux] = useState<Record<string, File>>({});
  const [values, setValues] = useState<Values>(() => initialValues(fields));
  const [errors, setErrors] = useState<Errors>({});
  const [problem, setProblem] = useState<{ title: string; error?: ApiError } | null>(null);
  const [uploaded, setUploaded] = useState<number | null>(null);
  const [run, setRun] = useState<{ job: Job; files: File[]; options: Record<string, unknown> } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [target, setTarget] = useState("");
  // Targets from a 422 TARGET_REQUIRED, valid only for the file list the server saw.
  const [serverTargets, setServerTargets] = useState<{ items: Item[]; targets: Target[] } | null>(null);
  // Page-grid entries, valid only for the file they were read from.
  const [pages, setPages] = useState<{ id: number; entries: Entry[] } | null>(null);
  const upload = useRef<AbortController | null>(null);
  const formRef = useRef<HTMLFormElement>(null);

  // A file dropped while uploading or watching a job must not make the browser open it and
  // leave the page; FilePicker adds files only when it is mounted and unlocked.
  useEffect(() => {
    const stop = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes("Files")) e.preventDefault();
    };
    window.addEventListener("dragover", stop);
    window.addEventListener("drop", stop);
    return () => {
      window.removeEventListener("dragover", stop);
      window.removeEventListener("drop", stop);
      upload.current?.abort(); // leaving the page cancels an unfinished upload
    };
  }, []);

  const { maxBytes: MAX_UPLOAD_BYTES, maxFiles } = useLimits();
  const { min } = tool.files;
  const max = Math.min(tool.files.max, maxFiles);
  const total = items.reduce((n, i) => n + i.file.size, 0);
  const auxSlots = Object.entries(tool.files.aux);
  const uploading = uploaded !== null;
  const forced = serverTargets?.items === items && serverTargets.targets.length > 0 ? serverTargets.targets : null;
  const plan: TargetPlan = forced
    ? { kind: "choose", targets: forced }
    : planTarget(tool, items.map((i) => i.file.name), formats);
  // A choice the current files can no longer reach counts as no choice.
  const chosen = plan.kind === "choose" && plan.targets.some((t) => t.id === target) ? target : "";
  const blocked = (plan.kind === "choose" && !chosen) || plan.kind === "conflict";
  const gridItem = grid ? items[0] : undefined;
  const gridEntries = gridItem && pages?.id === gridItem.id ? pages.entries : null;

  function setValue(name: string, v: string | boolean) {
    setValues((prev) => ({ ...prev, [name]: v }));
    setErrors((e) => omit(e, name));
  }

  function pickAux(slot: string, file: File | undefined) {
    const spec = tool.files.aux[slot];
    setErrors((e) => omit(e, `aux:${slot}`));
    if (file && !fileMatches(file.name, spec.formats, formats)) {
      const names = spec.formats.map((f) => formatTitle(f, formats)).join(" or ");
      setErrors((e) => ({ ...e, [`aux:${slot}`]: `${file.name} is not a ${names} file.` }));
      return;
    }
    setAux((a) => (file ? { ...a, [slot]: file } : omit(a, slot)));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (uploading) return;
    setProblem(null);
    const errs = validate(fields, values);
    for (const [slot, spec] of auxSlots) {
      if (spec.required && !aux[slot]) errs[`aux:${slot}`] = `Choose a file for ${spec.title}.`;
    }
    if (grid && gridItem) {
      if (!gridEntries) errs[grid.name] = "The pages are not ready to arrange yet.";
      else if (toPlan(gridEntries).length === 0) errs[grid.name] = "Keep at least one page: every page is marked deleted.";
    }
    setErrors(errs);
    if (items.length < min) {
      setProblem({ title: `Add ${min === 1 ? "a file" : `at least ${min} files`} first.` });
      document.getElementById("choose-files")?.focus();
      return;
    }
    if (total > MAX_UPLOAD_BYTES) {
      setProblem({ title: `The files add up to ${formatBytes(total)}; one job takes up to ${formatBytes(MAX_UPLOAD_BYTES)}.` });
      return;
    }
    if (blocked) return;
    if (Object.keys(errs).length) {
      formRef.current
        ?.querySelector<HTMLElement>(
          grid && errs[grid.name] ? ".pg-list [tabindex='0']" : ".field:has(.err) :is(input, select)",
        )
        ?.focus();
      setAnnouncement(`${Object.keys(errs).length} options need attention.`);
      return;
    }
    const ac = new AbortController();
    upload.current = ac;
    setUploaded(0);
    setAnnouncement("Uploading.");
    const files = items.map((i) => i.file);
    const options = buildOptions(fields, values);
    if (grid && gridEntries) options[grid.name] = toPlan(gridEntries);
    try {
      const created = await createJob(
        tool.id,
        {
          files,
          aux,
          options,
          target: plan.kind === "choose" ? chosen : undefined,
        },
        (loaded) => setUploaded(loaded),
        ac.signal,
      );
      // Secrets leave memory as soon as the server has them.
      setValues((v) => {
        const next = { ...v };
        for (const f of fields) if (f.kind === "password") next[f.name] = "";
        return next;
      });
      // Kept for the result card (target-size verdict), without any secret.
      const kept = { ...options };
      for (const f of fields) if (f.kind === "password") delete kept[f.name];
      setRun({ job: created, files, options: kept });
    } catch (err) {
      if (ac.signal.aborted) {
        setAnnouncement("Upload cancelled.");
      } else {
        const error = err instanceof ApiError ? err : new ApiError(0, "CLIENT", String(err));
        if (error.code === "INVALID_OPTIONS") setErrors(serverFieldErrors(error));
        if (error.code === "TARGET_REQUIRED") {
          setServerTargets({ items, targets: targetsFromServer(error.details.targets, tool.converts) });
          setErrors((e) => ({ ...e, target: "Choose a format to convert to." }));
        }
        if (error.code === "INVALID_TARGET") setTarget("");
        setProblem({ title: errorTitle(error.code, "Could not start the job."), error });
      }
    } finally {
      upload.current = null;
      setUploaded(null);
    }
  }

  if (run) {
    return (
      <div className="tool-run">
        <JobView
          key={run.job.id}
          initial={run.job}
          toolTitle={tool.title}
          onStartOver={() => setRun(null)}
          files={run.files}
          options={run.options}
        />
      </div>
    );
  }

  const short = items.length < min;
  return (
    <form className="tool-grid" ref={formRef} noValidate onSubmit={submit} aria-label={tool.title}>
      <p className="sr-only" role="status">
        {announcement}
      </p>
      <div>
        {tool.files.max === 0 ? (
          <p className="help">This tool needs no files: set the options and run it.</p>
        ) : (
          <FilePicker
            tool={tool}
            formats={formats}
            items={items}
            onItems={setItems}
            uploaded={uploaded}
            onCancelUpload={() => upload.current?.abort()}
            announce={setAnnouncement}
            locked={uploading}
          />
        )}
        {grid && gridItem && (
          <PageGrid
            key={gridItem.id}
            file={gridItem.file}
            entries={gridEntries}
            onEntries={(entries) => {
              setPages({ id: gridItem.id, entries });
              setErrors((e) => omit(e, grid.name));
            }}
            maxPages={grid.maxItems}
            error={errors[grid.name]}
            disabled={uploading}
          />
        )}
      </div>
      <div className="options-col">
        <div className="options">
          <h2 className="section-title">Options</h2>
          {fields.length === 0 && auxSlots.length === 0 && plan.kind === "none" && !grid && (
            <p className="help">This tool has no options. It runs as it is.</p>
          )}
          {grid && (
            <p className="help">
              {gridEntries
                ? `Arrange the pages in the grid. The result will have ${toPlan(gridEntries).length} pages.`
                : "Add a PDF to see its pages here as a grid you can arrange."}
            </p>
          )}
          <TargetField
            plan={plan}
            formats={formats}
            value={chosen}
            error={errors.target}
            disabled={uploading}
            onChange={(id) => {
              setTarget(id);
              setProblem(null);
              setErrors((e) => omit(e, "target"));
            }}
          />
          <OptionsForm fields={fields} values={values} errors={errors} disabled={uploading} onChange={setValue} />
          {auxSlots.map(([slot, spec]) => (
            <AuxField
              key={slot}
              slot={slot}
              title={spec.title}
              required={spec.required}
              accept={acceptAttr(spec.formats, formats)}
              file={aux[slot]}
              error={errors[`aux:${slot}`]}
              disabled={uploading}
              onPick={pickAux}
            />
          ))}
        </div>
        <div className="submit-bar">
          {problem && <Alert title={problem.title} error={problem.error} />}
          <p className="fsize">
            {uploading
              ? `Uploading ${total ? Math.round(((uploaded ?? 0) / total) * 100) : 0}% of ${formatBytes(total)}`
              : tool.files.max === 0
                ? "No files needed"
                : items.length === 0
                ? `Add ${min > 1 ? `${min} or more files` : "a file"} to start`
                : `${items.length} ${items.length === 1 ? "file" : "files"}, ${formatBytes(total)}` +
                  (short
                    ? ` · add ${min - items.length} more`
                    : plan.kind === "choose" && !chosen
                      ? " · choose a format"
                      : max > 1 && items.length >= max
                        ? " · list full"
                        : "")}
          </p>
          {uploading ? (
            <button type="button" className="btn btn-secondary btn-block" onClick={() => upload.current?.abort()}>
              <Icon name="x" size={20} />
              Cancel upload
            </button>
          ) : (
            <button type="submit" className="btn btn-primary btn-block" disabled={blocked}>
              {tool.title} <span aria-hidden="true">→</span>
            </button>
          )}
        </div>
      </div>
    </form>
  );
}

function omit<T>(obj: Record<string, T>, key: string): Record<string, T> {
  const next = { ...obj };
  delete next[key];
  return next;
}

/** INVALID_OPTIONS details.errors[].loc → a message per top-level field. */
function serverFieldErrors(error: ApiError): Errors {
  const out: Errors = {};
  const list = Array.isArray(error.details.errors) ? error.details.errors : [];
  for (const e of list as { loc?: unknown[]; message?: unknown }[]) {
    const name = e.loc?.find((p) => typeof p === "string" && p !== "body");
    if (typeof name === "string" && typeof e.message === "string") out[name] = e.message;
  }
  return out;
}

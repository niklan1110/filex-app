import { useEffect } from "react";
import { paths, type ToolDetail } from "../api/client";
import { useApi } from "../api/useApi";
import { acceptsInWords, category, type FormatIndex } from "../catalog";
import { Alert, loadFailedTitle } from "../components/Alert";
import { ConvertsHint } from "../components/ConvertsHint";
import { Icon } from "../components/Icon";
import { Link } from "../router";
import { RunTool } from "./RunTool";

// Header field, accepted input and availability; RunTool is the two-column body.
export function ToolPage({ id, formats }: { id: string; formats: FormatIndex }) {
  const { data: tool, error, loading, retry } = useApi<ToolDetail>(paths.tool(id));

  useEffect(() => {
    document.title = tool ? `${tool.title} · FileX` : "FileX";
    return () => {
      document.title = "FileX";
    };
  }, [tool]);

  if (loading) {
    return (
      <>
        <h1 className="sr-only">Tool</h1>
        <p className="state" role="status">Loading the tool…</p>
      </>
    );
  }
  if (error || !tool) {
    const missing = error?.code === "TOOL_NOT_FOUND";
    return (
      <>
        <h1 className="page-title">{missing ? "No such tool" : "Tool"}</h1>
        <div className="state">
          {missing ? (
            <p>
              There is no tool called <code>{id}</code> on this server. <Link to="/">See all tools</Link>
            </p>
          ) : (
            error && (
              <Alert title={loadFailedTitle(error, "this tool")} error={error}>
                <button type="button" className="btn btn-secondary btn-sm" onClick={retry}>
                  Try again
                </button>
              </Alert>
            )
          )}
        </div>
      </>
    );
  }

  const cat = category(tool.category);
  return (
    <>
      <div className={`tool-head field-surface folded cat-${tool.category}`}>
        <nav className="crumb" aria-label="Breadcrumb">
          <Link to="/">Tools</Link> / {cat.band}
        </nav>
        <div className="tool-title-row">
          <h1 className="tool-title">{tool.title}</h1>
          <p>{acceptsInWords(tool.inputs, tool.files, formats)}</p>
        </div>
      </div>
      <p className="tool-summary">{tool.summary}</p>
      <ConvertsHint tool={tool} as="p" />

      {tool.available ? (
        <RunTool tool={tool} formats={formats} />
      ) : (
        <div className="notice state" role="note">
          <Icon name="lock" size={18} />
          <div>
            <strong>This tool is off on this server.</strong>
            <p>
              Enable the <code>{tool.profile}</code> profile: add <code>{tool.profile}</code> to{" "}
              <code>COMPOSE_PROFILES</code> in <code>.env</code>, then run <code>docker compose up -d</code>.
              The tool turns on once its worker is running.
            </p>
          </div>
        </div>
      )}
    </>
  );
}

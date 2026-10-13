// Sign-in column and its states (design.md "Sign-in"): Keel email + password, forgot password,
// set password (invite or reset link), token mode, not allowed, sign-in server unreachable.
// There is no sign-up form: accounts are invite-only (review C1).
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Icon } from "../components/Icon";

export type Problem = { title: string; text?: string };

export type SignInView =
  | { kind: "signin"; note?: string; problem?: Problem }
  | { kind: "set-password"; invite: boolean }
  | { kind: "not-allowed" }
  | { kind: "unreachable" }
  | { kind: "switch"; next: string | null }; // an email link for another account (review final L-1)

type Props = {
  mode: "KEEL" | "TOKEN";
  view: SignInView;
  email: string | null;
  onSignIn: (email: string, password: string) => Promise<Problem | null>;
  onToken: (token: string) => Promise<Problem | null>;
  onForgot: (email: string) => Promise<Problem | null>;
  onSetPassword: (password: string) => Promise<Problem | null>;
  onRetry: () => void;
  onSignOut: () => void;
  onSwitch: (accept: boolean) => Promise<void>;
};

export const MIN_PASSWORD = 12; // review C1 / ADR-0009 owner checklist; Keel checks it too
const MAX_PASSWORD = 72;

export function SignIn(props: Props) {
  const { view } = props;
  const [forgot, setForgot] = useState(false);
  const headRef = useRef<HTMLHeadingElement>(null);
  const screen = view.kind === "signin" && forgot ? "forgot" : view.kind;

  // A new state is a new page for keyboard and screen-reader users: focus its title.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    headRef.current?.focus();
  }, [screen]);

  let title = "Sign in";
  let body: ReactNode;
  if (view.kind === "not-allowed") {
    body = (
      <>
        <Alert title="This account is not on the FileX list. Ask your admin to add it." />
        {props.email && <p className="si-gap muted">Signed in as <span className="mono">{props.email}</span>.</p>}
        <button type="button" className="btn btn-primary btn-block si-submit" onClick={props.onSignOut}>
          Sign out
        </button>
      </>
    );
  } else if (view.kind === "unreachable") {
    body = (
      <>
        <Alert title="Sign-in server unreachable" text="FileX could not reach Keel. Check that it is running, then try again." />
        <button type="button" className="btn btn-primary btn-block si-submit" onClick={props.onRetry}>
          Try again
        </button>
        <button type="button" className="btn-text si-gap" onClick={props.onSignOut}>
          Sign out
        </button>
      </>
    );
  } else if (view.kind === "switch") {
    title = "Switch account?";
    body = <SwitchPrompt current={props.email} next={view.next} onDecide={props.onSwitch} />;
  } else if (view.kind === "set-password") {
    title = view.invite ? "Set your password" : "Choose a new password";
    body = <SetPasswordForm email={props.email} onSubmit={props.onSetPassword} />;
  } else if (forgot) {
    title = "Reset your password";
    body = <ForgotForm onSubmit={props.onForgot} onBack={() => setForgot(false)} />;
  } else if (props.mode === "TOKEN") {
    body = <TokenForm view={view} onSubmit={props.onToken} />;
  } else {
    body = <PasswordForm view={view} onSubmit={props.onSignIn} onForgot={() => setForgot(true)} />;
  }

  return (
    <main className="signin" id="main">
      <div className="si-panel folded">
        <div className="si-brand">
          <span className="mark folded" aria-hidden="true">
            <Icon name="logo" size={14} />
          </span>
          FileX
        </div>
        <div>
          <p className="wordmark">
            Your paper,
            <br />
            your server.
          </p>
          <p className="si-sub">Every tool runs on this machine. Files never leave your network.</p>
        </div>
      </div>
      <div className="si-form-wrap">
        <div className="si-form">
          <h1 className="page-title" tabIndex={-1} ref={headRef}>
            {title}
          </h1>
          {body}
        </div>
      </div>
    </main>
  );
}

function Alert({ title, text }: Problem) {
  return (
    <div className="alert si-gap" role="alert">
      <Icon name="alert" size={18} />
      <div>
        <strong>{title}</strong>
        {text && <p>{text}</p>}
      </div>
    </div>
  );
}

/** Runs one async submit at a time and keeps its problem; null means it worked. */
function useSubmit<A extends unknown[]>(fn: (...a: A) => Promise<Problem | null>) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [ran, setRan] = useState(false); // after the first submit, the view's own problem is stale
  async function run(...a: A): Promise<boolean> {
    setBusy(true);
    setRan(true);
    setProblem(null);
    try {
      const p = await fn(...a);
      setProblem(p);
      return p === null;
    } finally {
      setBusy(false);
    }
  }
  return { busy, problem, setProblem, run, ran };
}

function PasswordForm({ view, onSubmit, onForgot }: {
  view: Extract<SignInView, { kind: "signin" }>;
  onSubmit: Props["onSignIn"];
  onForgot: () => void;
}) {
  const id = useId();
  const [show, setShow] = useState(false);
  const { busy, problem, run, ran } = useSubmit(onSubmit);
  const shown = ran ? problem : view.problem;

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    void run(String(f.get("email")).trim(), String(f.get("password")));
  }

  return (
    <form onSubmit={submit} aria-busy={busy || undefined}>
      <p className="muted si-lead">Use the account your admin invited.</p>
      {view.note && !shown && !ran && <p className="si-note si-gap" role="status">{view.note}</p>}
      {shown && <Alert {...shown} />}
      <div className="si-field">
        <label className="label" htmlFor={`${id}-email`}>Email</label>
        <input className="input" id={`${id}-email`} name="email" type="email" autoComplete="username" required />
      </div>
      <div className="si-field">
        <div className="si-label-row">
          <label className="label" htmlFor={`${id}-pw`}>Password</label>
          <button type="button" className="btn-text" onClick={onForgot}>Forgot password?</button>
        </div>
        <div className="affix">
          <input className="input" id={`${id}-pw`} name="password" type={show ? "text" : "password"}
            autoComplete="current-password" required />
          <button type="button" className="affix-btn" aria-label="Show password" aria-pressed={show}
            onClick={() => setShow((s) => !s)}>
            <Icon name="eye" size={18} />
          </button>
        </div>
      </div>
      <button type="submit" className="btn btn-primary btn-block si-submit" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

function TokenForm({ view, onSubmit }: { view: Extract<SignInView, { kind: "signin" }>; onSubmit: Props["onToken"] }) {
  const id = useId();
  const { busy, problem, setProblem, run, ran } = useSubmit(onSubmit);
  const shown = ran || problem ? problem : view.problem;

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const token = String(new FormData(e.currentTarget).get("token")).trim();
    if (!token.startsWith("fxt_")) {
      setProblem({ title: "That is not a FileX access token.", text: "Access tokens start with fxt_." });
      return;
    }
    void run(token);
  }

  return (
    <form onSubmit={submit} aria-busy={busy || undefined}>
      <p className="muted si-lead">Sign-in by account is off on this server. Paste the access token your admin gave you.</p>
      {view.note && !shown && !ran && <p className="si-note si-gap" role="status">{view.note}</p>}
      {shown && <Alert {...shown} />}
      <div className="si-field">
        <label className="label" htmlFor={`${id}-token`}>Access token</label>
        <input className="input mono" id={`${id}-token`} name="token" type="password" autoComplete="off"
          spellCheck={false} required aria-describedby={`${id}-token-help`} />
        <span className="help" id={`${id}-token-help`}>Kept in this tab only, until you close it or sign out.</span>
      </div>
      <button type="submit" className="btn btn-primary btn-block si-submit" disabled={busy}>
        {busy ? "Checking…" : "Sign in"}
      </button>
    </form>
  );
}

function ForgotForm({ onSubmit, onBack }: { onSubmit: Props["onForgot"]; onBack: () => void }) {
  const id = useId();
  const [sentTo, setSentTo] = useState<string | null>(null);
  const { busy, problem, run } = useSubmit(onSubmit);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const email = String(new FormData(e.currentTarget).get("email")).trim();
    if (await run(email)) setSentTo(email);
  }

  return (
    <form onSubmit={submit} aria-busy={busy || undefined}>
      <p className="muted si-lead">We will email you a link to choose a new password.</p>
      {sentTo && (
        <p className="si-note si-gap" role="status">
          If {sentTo} has an account, a reset link is on its way. It works once, for an hour.
        </p>
      )}
      {problem && <Alert {...problem} />}
      <div className="si-field">
        <label className="label" htmlFor={`${id}-email`}>Email</label>
        <input className="input" id={`${id}-email`} name="email" type="email" autoComplete="username" required />
      </div>
      <button type="submit" className="btn btn-primary btn-block si-submit" disabled={busy}>
        {busy ? "Sending…" : "Send reset link"}
      </button>
      <button type="button" className="btn-text si-gap" onClick={onBack}>Back to sign in</button>
    </form>
  );
}

// Staying is the primary, safe answer: anyone on the FileX list can send a link to their own account.
function SwitchPrompt({ current, next, onDecide }: { current: string | null; next: string | null; onDecide: Props["onSwitch"] }) {
  const [busy, setBusy] = useState(false);
  async function decide(accept: boolean) {
    setBusy(true);
    try {
      await onDecide(accept);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div aria-busy={busy || undefined}>
      <p className="si-lead">
        You are signed in as <span className="mono">{current ?? "another account"}</span>. This email link
        signs in as <span className="mono">{next ?? "a different account"}</span>.
      </p>
      <p className="si-note si-gap">
        Switch only if you asked for this link. If you did not, stay signed in: files you upload after
        switching go to the other account.
      </p>
      <button type="button" className="btn btn-primary btn-block si-submit" disabled={busy} onClick={() => void decide(false)}>
        Stay signed in
      </button>
      <button type="button" className="btn btn-secondary btn-block si-gap" disabled={busy} onClick={() => void decide(true)}>
        Switch account
      </button>
    </div>
  );
}

function SetPasswordForm({ email, onSubmit }: { email: string | null; onSubmit: Props["onSetPassword"] }) {
  const id = useId();
  const { busy, problem, setProblem, run } = useSubmit(onSubmit);
  const [bad, setBad] = useState<"pw" | "confirm" | null>(null);

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const pw = String(f.get("password"));
    if (pw.length < MIN_PASSWORD || pw.length > MAX_PASSWORD) {
      setBad("pw");
      setProblem({ title: `Use ${MIN_PASSWORD} to ${MAX_PASSWORD} characters.` });
      return;
    }
    if (pw !== String(f.get("confirm"))) {
      setBad("confirm");
      setProblem({ title: "The two passwords do not match." });
      return;
    }
    setBad(null);
    void run(pw);
  }

  return (
    <form onSubmit={submit} aria-busy={busy || undefined} noValidate>
      <p className="muted si-lead">
        {email ? (
          <>Signed in as <span className="mono">{email}</span> from your email link.</>
        ) : (
          "You are signed in from your email link."
        )}{" "}
        Choose a password for next time.
      </p>
      {problem && <Alert {...problem} />}
      <div className="si-field">
        <label className="label" htmlFor={`${id}-pw`}>New password</label>
        <input className="input" id={`${id}-pw`} name="password" type="password" autoComplete="new-password"
          required aria-invalid={bad === "pw" || undefined} aria-describedby={`${id}-rules`} />
        <span className="help" id={`${id}-rules`}>
          {MIN_PASSWORD} to {MAX_PASSWORD} characters. A few unrelated words work well.
        </span>
      </div>
      <div className="si-field">
        <label className="label" htmlFor={`${id}-confirm`}>Confirm password</label>
        <input className="input" id={`${id}-confirm`} name="confirm" type="password" autoComplete="new-password"
          required aria-invalid={bad === "confirm" || undefined} />
      </div>
      <button type="submit" className="btn btn-primary btn-block si-submit" disabled={busy}>
        {busy ? "Saving…" : "Save password and continue"}
      </button>
    </form>
  );
}

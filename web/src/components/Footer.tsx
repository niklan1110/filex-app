// AGPL-3.0 section 13: anyone using FileX over a network must be able to get its source.
// A fork that modifies FileX must point this at its own source: build with
//   VITE_FILEX_SOURCE_URL=https://example.org/your/fork npm run build
export const SOURCE_URL: string = import.meta.env.VITE_FILEX_SOURCE_URL || "https://github.com/niklan1110/filex-app";

// LICENSE and THIRD_PARTY_NOTICES.md are not in web/public yet, so these point at the repository.
// Once they are served, use "/LICENSE" and "/THIRD_PARTY_NOTICES.md" so they follow the deployed build.
export const LICENSE_URL = `${SOURCE_URL}/blob/main/LICENSE`;
export const NOTICES_URL = `${SOURCE_URL}/blob/main/THIRD_PARTY_NOTICES.md`;

export function Footer({ version }: { version?: string }) {
  return (
    <footer className="app-footer">
      <p className="inner">
        <span>FileX{version ? ` ${version}` : ""}</span>
        <span aria-hidden="true">·</span>
        <a href={LICENSE_URL} aria-label="AGPL-3.0 licence">
          AGPL-3.0
        </a>
        <span aria-hidden="true">·</span>
        <a href={SOURCE_URL}>Source code</a>
        <span aria-hidden="true">·</span>
        <a href={NOTICES_URL}>Third-party notices</a>
      </p>
    </footer>
  );
}

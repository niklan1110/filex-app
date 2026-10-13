import sprite from "../assets/icons.svg?url";

export type IconName =
  | "search" | "merge" | "split" | "rotate" | "grid" | "compress" | "wrench" | "convert"
  | "image" | "text" | "scan" | "sparkle" | "drop" | "stamp" | "hash" | "lock" | "pen"
  | "eraser" | "check" | "x" | "grip" | "up" | "down" | "file" | "download" | "trash"
  | "clock" | "alert" | "eye" | "arrow" | "logo" | "copy" | "undo";

/** Decorative sprite icon. Sizes per design.md: 24 tiles, 20 buttons, 18 rows, 16 inline, 14 badges. */
export function Icon({ name, size = 24 }: { name: IconName; size?: 14 | 16 | 18 | 20 | 24 | 26 }) {
  return (
    <svg className="ico" width={size} height={size} aria-hidden="true" focusable="false">
      <use href={`${sprite}#i-${name}`} />
    </svg>
  );
}

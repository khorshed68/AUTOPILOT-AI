import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { ApiError } from "@/services/api";

export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs));

/** Everything is displayed in the user's saved timezone, the same one scheduling uses. */
let displayZone: string | undefined;
export const setDisplayTimezone = (zone: string | undefined) => { displayZone = zone; };

const parts = (timeZone: string | undefined, options: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(undefined, { timeZone: timeZone ?? displayZone, ...options });

/** A Date whose local fields read as the wall clock in the display timezone. For layout and form values only. */
export const zonedDate = (value: string | Date) => new Date(new Date(value).toLocaleString("en-US", { timeZone: displayZone }));

/** Convert a wall-clock "YYYY-MM-DDTHH:mm" in the display timezone to a UTC instant. */
export function zonedInputToIso(local: string): string {
  if (!displayZone) return new Date(local).toISOString();
  const wall = Date.parse(`${local}:00Z`);
  let instant = wall;
  for (let i = 0; i < 2; i++) {
    const shown = zonedDate(new Date(instant));
    instant += wall - Date.UTC(shown.getFullYear(), shown.getMonth(), shown.getDate(), shown.getHours(), shown.getMinutes());
  }
  return new Date(instant).toISOString();
}

export const fmtDateTime = (value: string | null | undefined, tz?: string) =>
  value ? parts(tz, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "—";
export const fmtDate = (value: string | null | undefined, tz?: string) => (value ? parts(tz, { day: "numeric", month: "short", year: "numeric" }).format(new Date(value)) : "—");
export const fmtTime = (value: string, tz?: string) => parts(tz, { hour: "2-digit", minute: "2-digit" }).format(new Date(value));

export function timeAgo(value: string): string {
  const seconds = (Date.now() - new Date(value).getTime()) / 1000;
  if (seconds < 0) return fmtDateTime(value);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)}d ago`;
  return fmtDate(value);
}

export const fmtBytes = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
export const titleCase = (value: string) => value.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
export const errorMessage = (error: unknown) => (error instanceof ApiError || error instanceof Error ? error.message : "Something went wrong.");

/** Value for <input type="datetime-local"> from a browser-local Date. */
export function toLocalInput(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;
export const initials = (name: string) => name.split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();

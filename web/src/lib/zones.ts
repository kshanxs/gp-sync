// Time zones for Settings: every zone the browser knows, with its offset from UTC right now.

// Old names some browsers still list, and their current names (the server knows both).
const RENAMED: Record<string, string> = {
  "Asia/Calcutta": "Asia/Kolkata",
  "Asia/Katmandu": "Asia/Kathmandu",
  "Asia/Saigon": "Asia/Ho_Chi_Minh",
  "Asia/Rangoon": "Asia/Yangon",
  "Europe/Kiev": "Europe/Kyiv",
  "Atlantic/Faeroe": "Atlantic/Faroe",
  "Pacific/Truk": "Pacific/Chuuk",
  "Pacific/Ponape": "Pacific/Pohnpei",
  "Pacific/Enderbury": "Pacific/Kanton",
  "America/Godthab": "America/Nuuk",
};

/** The current name of a zone ("Asia/Calcutta" → "Asia/Kolkata"). */
export function currentName(zone: string): string {
  return RENAMED[zone] ?? zone;
}

/** "India Standard Time", "Eastern Standard Time", or "" if the browser has no name for it. */
export function zoneLongName(zone: string, at = new Date()): string {
  try {
    const name = new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: "long" })
      .formatToParts(at)
      .find((p) => p.type === "timeZoneName")?.value;
    return name && !name.startsWith("GMT") ? name : "";
  } catch {
    return "";
  }
}

/** Minutes east of UTC in `zone` at `at` (daylight saving included), or null if unknown. */
export function zoneOffset(zone: string, at = new Date()): number | null {
  try {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: "longOffset" })
      .formatToParts(at)
      .find((p) => p.type === "timeZoneName")?.value;
    const m = /GMT(?:([+-])(\d{1,2})(?::(\d{2}))?)?/.exec(parts ?? "");
    if (!m) return null;
    if (!m[1]) return 0;
    const mins = Number(m[2]) * 60 + Number(m[3] ?? 0);
    return m[1] === "-" ? -mins : mins;
  } catch {
    return null;
  }
}

/** "UTC+05:30", "UTC−04:00", "UTC". */
export function offsetLabel(min: number): string {
  if (!min) return "UTC";
  const a = Math.abs(min);
  return `UTC${min < 0 ? "−" : "+"}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
}

/** "Asia/Kolkata" → "Kolkata (Asia)", "America/Argentina/Buenos_Aires" → "Buenos Aires (America)". */
export function zoneTitle(zone: string): string {
  const parts = zone.split("/");
  if (parts.length < 2) return zone.replace(/_/g, " ");
  return `${parts[parts.length - 1]!.replace(/_/g, " ")} (${parts[0]})`;
}

export interface ZoneOption {
  zone: string;
  offset: number;
  label: string;
}

/** Every zone, west to east, then by name. */
export function zoneOptions(at = new Date()): ZoneOption[] {
  const names =
    (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.("timeZone") ??
    [];
  const list = [...new Set([...names, "UTC"].map(currentName))];
  return list
    .map((zone) => {
      const offset = zoneOffset(zone, at) ?? 0;
      const long = zoneLongName(zone, at);
      return {
        zone,
        offset,
        label: `(${offsetLabel(offset)}) ${zoneTitle(zone)}${long ? ` · ${long}` : ""}`,
      };
    })
    .sort((a, b) => a.offset - b.offset || a.label.localeCompare(b.label));
}

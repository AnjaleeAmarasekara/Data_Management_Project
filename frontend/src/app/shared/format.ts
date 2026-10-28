const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad = (n: number) => String(n).padStart(2, '0');

/** Timestamps are shown in UTC, the same clock the backend and database use. */
export function fmtTs(iso: string, withDate = true): string {
  const d = new Date(iso);
  const time = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  return withDate ? `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${time}` : time;
}

export function downloadCsv(filename: string, rows: (string | number | null)[][]): void {
  const escape = (v: string | number | null) => {
    const s = v === null ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const blob = new Blob([rows.map((r) => r.map(escape).join(',')).join('\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

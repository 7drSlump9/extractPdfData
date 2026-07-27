import React from 'react';

const COLORS = [
  'rgba(239,68,68,0.2)', 'rgba(34,197,94,0.2)', 'rgba(59,130,246,0.2)',
  'rgba(234,179,8,0.2)', 'rgba(168,85,247,0.2)', 'rgba(6,182,212,0.2)',
  'rgba(249,115,22,0.2)', 'rgba(236,72,153,0.2)',
];
const BORDERS = [
  '#ef4444', '#22c55e', '#3b82f6',
  '#eab308', '#a855f7', '#06b6d4',
  '#f97316', '#ec4899',
];

export default function ZoneOverlay({ template, scale }) {
  if (!template || !scale) return null;

  const headerFields = Array.isArray(template.header_fields) ? template.header_fields : [];
  const tableColumns = Array.isArray(template.table?.columns) ? template.table.columns : [];

  const toCanvas = (v) => (Number.isFinite(Number(v)) ? Number(v) * scale : 0);

  const zones = [];

  headerFields.forEach((field, idx) => {
    const x = toCanvas(field.x_min ?? 0);
    const y = toCanvas(field.y_min ?? 0);
    const w = Math.max(Math.abs(toCanvas(field.x_max ?? 200) - x), 20);
    const h = Math.max(Math.abs(toCanvas(field.y_max ?? 40) - y), 10);
    zones.push({
      key: `h-${idx}`,
      x, y, w, h,
      color: COLORS[idx % COLORS.length],
      border: BORDERS[idx % BORDERS.length],
      label: (field.field || field.regex || `h${idx}`).substring(0, 20),
    });
  });

  tableColumns.forEach((col, idx) => {
    const x = toCanvas(col.x_min ?? (100 + idx * 80));
    const w = Math.max(Math.abs(toCanvas(col.x_max ?? (180 + idx * 80)) - x), 30);
    zones.push({
      key: `c-${idx}`,
      x, y: 0, w, h: 40,
      color: COLORS[(headerFields.length + idx) % COLORS.length],
      border: BORDERS[(headerFields.length + idx) % BORDERS.length],
      label: (col.header || col.regex || `col${idx}`).substring(0, 20),
    });
  });

  return (
    <div style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none' }}>
      {zones.map(z => (
        <div key={z.key} style={{
          position: 'absolute',
          left: z.x,
          top: z.y,
          width: z.w,
          height: z.h,
          border: `2px solid ${z.border}`,
          backgroundColor: z.color,
          pointerEvents: 'none',
          fontSize: 10,
          overflow: 'hidden',
          zIndex: 20,
        }}>
          <span style={{
            background: 'rgba(0,0,0,0.7)',
            color: '#fff',
            padding: '1px 4px',
            whiteSpace: 'nowrap',
            borderRadius: 2,
            lineHeight: '14px',
          }}>
            {z.label}
          </span>
        </div>
      ))}
    </div>
  );
}
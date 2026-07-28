import React, { useState } from 'react';

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

const SECTION_COLORS = {
  header: { bg: 'rgba(59,130,246,0.08)', border: '#3b82f6' },
  table: { bg: 'rgba(34,197,94,0.08)', border: '#22c55e' },
  footer: { bg: 'rgba(239,68,68,0.08)', border: '#ef4444' },
};

export default function ZoneOverlay({ template, scale, yOffset = 0, canvasScaleX = 1, canvasScaleY = 1 }) {
  const [showZones, setShowZones] = useState(true);
  if (!template || !scale) return null;

  const toCanvas = (v) => (Number.isFinite(Number(v)) ? Number(v) * scale : 0);
  const cssScaleX = canvasScaleX || 1;
  const cssScaleY = canvasScaleY || 1;
  const offsetY = toCanvas(yOffset);

  const cssLeft = (v) => (Number.isFinite(Number(v)) ? Number(v) / cssScaleX : 0);
  const cssTop = (v) => (Number.isFinite(Number(v)) ? Number(v) / cssScaleY : 0);
  const cssW = (v) => (Number.isFinite(Number(v)) ? Number(v) / cssScaleX : 0);
  const cssH = (v) => (Number.isFinite(Number(v)) ? Number(v) / cssScaleY : 0);

  const zones = [];
  const sections = [];

  // --- Header section ---
  const hdr = template.header;
  if (hdr) {
    const hdrX = toCanvas(hdr.x_min ?? 0);
    const hdrW = Math.max(Math.abs(toCanvas(hdr.x_max ?? 0) - hdrX), 10);
    const hdrY = toCanvas(hdr.y_min ?? 0) + offsetY;
    const hdrH = Math.max(Math.abs(toCanvas(hdr.y_max ?? 200) - hdrY), 10);
    sections.push({
      key: 'sec-hdr', x: hdrX, w: hdrW, y: hdrY, h: hdrH,
      bg: SECTION_COLORS.header.bg, border: SECTION_COLORS.header.border,
      label: 'HEADER',
    });
    (hdr.fields || []).forEach((f, idx) => {
      const x = toCanvas(f.x_min ?? 0);
      const y = toCanvas(f.y_min ?? 0) + offsetY;
      const w = Math.max(Math.abs(toCanvas(f.x_max ?? 200) - x), 20);
      const h = Math.max(Math.abs(toCanvas(f.y_max ?? 100) - y), 10);
      zones.push({
        key: `h-${idx}`, x, y, w, h,
        color: COLORS[idx % COLORS.length],
        border: BORDERS[idx % BORDERS.length],
        label: (f.field || f.regex || `h${idx}`).substring(0, 20),
      });
    });
  }

  // --- Table section ---
  const tbl = template.table;
  if (tbl) {
    const tblY = toCanvas(tbl.y_min ?? 200) + offsetY;
    const tblH = Math.max(Math.abs(toCanvas(tbl.y_max ?? 600) - tblY), 10);
    sections.push({
      key: 'sec-tbl', y: tblY, h: tblH,
      bg: SECTION_COLORS.table.bg, border: SECTION_COLORS.table.border,
      label: 'TABLE',
    });
    (tbl.columns || []).forEach((col, idx) => {
      const x = toCanvas(col.x_min ?? (100 + idx * 80));
      const w = Math.max(Math.abs(toCanvas(col.x_max ?? (180 + idx * 80)) - x), 30);
      zones.push({
        key: `c-${idx}`, x, y: tblY, w, h: tblH,
        color: COLORS[(hdr?.fields?.length || 0 + idx) % COLORS.length],
        border: BORDERS[(hdr?.fields?.length || 0 + idx) % BORDERS.length],
        label: (col.header || col.regex || `col${idx}`).substring(0, 20),
      });
    });
  }

  // --- Footer section ---
  const ftr = template.footer;
  if (ftr) {
    const ftrY = toCanvas(ftr.y_min ?? 600) + offsetY;
    const ftrH = Math.max(Math.abs(toCanvas(ftr.y_max ?? 800) - ftrY), 10);
    sections.push({
      key: 'sec-ftr', y: ftrY, h: ftrH,
      bg: SECTION_COLORS.footer.bg, border: SECTION_COLORS.footer.border,
      label: 'FOOTER',
    });
    (ftr.fields || []).forEach((f, idx) => {
      const x = toCanvas(f.x_min ?? 0);
      const w = Math.max(Math.abs(toCanvas(f.x_max ?? 200) - x), 20);
      zones.push({
        key: `f-${idx}`, x, y: ftrY, w, h: ftrH,
        color: COLORS[idx % COLORS.length],
        border: BORDERS[idx % BORDERS.length],
        label: (f.field || f.regex || `f${idx}`).substring(0, 20),
      });
    });
  }

  return (
    <>
      {/* Toggle button */}
      <button
        onClick={() => setShowZones(z => !z)}
        style={{
          position: 'absolute', top: 4, right: 4, zIndex: 40,
          background: showZones ? '#22c55e' : '#475569',
          color: '#fff', border: 'none', borderRadius: 4,
          padding: '3px 8px', fontSize: 10, cursor: 'pointer',
        }}
      >
        {showZones ? 'Zone ON' : 'Zone OFF'}
      </button>

      {showZones && (
        <div style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none' }}>
          {/* Section backgrounds */}
          {sections.map(s => (
            <div key={s.key} style={{
              position: 'absolute',
              left: cssLeft(s.x ?? 0), top: cssTop(s.y),
              width: cssW(s.w ?? 0), height: cssH(s.h),
              backgroundColor: s.bg,
              border: `1px dashed ${s.border}`,
              pointerEvents: 'none',
              zIndex: 5,
            }}>
              <span style={{
                position: 'absolute', top: 2, left: 4,
                fontSize: 9, color: s.border, fontWeight: 'bold',
                background: 'rgba(0,0,0,0.5)', padding: '1px 4px', borderRadius: 2,
              }}>
                {s.label} [{Math.round(s.y / scale)}-{Math.round((s.y + s.h) / scale)}]
              </span>
            </div>
          ))}

          {/* Field/column zones */}
          {zones.map(z => (
            <div
              key={z.key}
              draggable={z.key.startsWith('h-')}
              onDragStart={z.key.startsWith('h-') ? (e) => {
                const idx = parseInt(z.key.split('-')[1], 10);
                e.dataTransfer.setData('application/tag-index', String(idx));
                e.dataTransfer.effectAllowed = 'move';
                // Drag ghost con le stesse proporzioni del rettangolo
                if (z.w > 0 && z.h > 0) {
                  const ghost = document.createElement('canvas');
                  const scale = Math.min(100 / z.w, 1);
                  ghost.width = Math.max(z.w * scale, 20);
                  ghost.height = Math.max(z.h * scale, 10);
                  const ctx = ghost.getContext('2d');
                  ctx.fillStyle = 'rgba(59,130,246,0.4)';
                  ctx.fillRect(0, 0, ghost.width, ghost.height);
                  ctx.strokeStyle = '#3b82f6';
                  ctx.lineWidth = 2;
                  ctx.strokeRect(0, 0, ghost.width, ghost.height);
                  e.dataTransfer.setDragImage(ghost, 0, 0);
                }
              } : undefined}
              style={{
                position: 'absolute',
                left: cssLeft(z.x), top: cssTop(z.y),
                width: cssW(z.w), height: cssH(z.h),
                border: `2px solid ${z.border}`,
                backgroundColor: z.color,
                pointerEvents: 'auto',
                cursor: z.key.startsWith('h-') ? 'grab' : 'default',
                fontSize: 10, overflow: 'hidden',
                zIndex: 20,
              }}
            >
              <span style={{
                background: 'rgba(0,0,0,0.7)',
                color: '#fff', padding: '1px 4px',
                whiteSpace: 'nowrap', borderRadius: 2,
                lineHeight: '14px',
              }}>
                {z.label}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
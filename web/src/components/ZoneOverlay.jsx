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

const RESIZE_HANDLE = {
  size: 6,
  style: {
    position: 'absolute',
    background: '#fff',
    border: '1px solid #000',
    zIndex: 30,
    pointerEvents: 'auto',
  },
};

const HANDLE_POSITIONS = {
  nw: { top: -3, left: -3, cursor: 'nw-resize' },
  n:  { top: -3, left: '50%', marginLeft: -3, cursor: 'n-resize' },
  ne: { top: -3, right: -3, cursor: 'ne-resize' },
  e:  { top: '50%', marginTop: -3, right: -3, cursor: 'e-resize' },
  se: { bottom: -3, right: -3, cursor: 'se-resize' },
  s:  { bottom: -3, left: '50%', marginLeft: -3, cursor: 's-resize' },
  sw: { bottom: -3, left: -3, cursor: 'sw-resize' },
  w:  { top: '50%', marginTop: -3, left: -3, cursor: 'w-resize' },
};

export default function ZoneOverlay({ template, scale, yOffset = 0, canvasScaleX = 1, canvasScaleY = 1, canvasRefWidth = 0, canvasRefHeight = 0, onHdrResize, onColResize, onFtrResize, onZoneClick, selectedZoneIndex, onFtrZoneClick, selectedFtrZoneIndex }) {
  const [showZones, setShowZones] = useState(true);
  const resizeRef = React.useRef(null);
  if (!template || !scale) return null;

  // Coordinate template in 0-1000, canvas in pixel. Converti.
  const canvasW = canvasRefWidth || 1653; // fallback A4 200dpi
  const canvasH = canvasRefHeight || 2339;
  const toCanvasX = (v) => (Number.isFinite(Number(v)) ? Number(v) / 1000 * canvasW : 0);
  const toCanvasY = (v) => (Number.isFinite(Number(v)) ? Number(v) / 1000 * canvasH : 0);
  const cssScaleX = canvasScaleX || 1;
  const cssScaleY = canvasScaleY || 1;
  const offsetY = toCanvasY(yOffset);

  const cssLeft = (v) => (Number.isFinite(Number(v)) ? Number(v) / cssScaleX : 0);
  const cssTop = (v) => (Number.isFinite(Number(v)) ? Number(v) / cssScaleY : 0);
  const cssW = (v) => (Number.isFinite(Number(v)) ? Number(v) / cssScaleX : 0);
  const cssH = (v) => (Number.isFinite(Number(v)) ? Number(v) / cssScaleY : 0);

  // Resize handlers
  const handleResizeStart = (e, fieldIdx, handle) => {
    e.preventDefault();
    e.stopPropagation();
    const fields = template?.header?.fields;
    if (!fields || fieldIdx >= fields.length) return;
    const f = fields[fieldIdx];
    const startX = e.clientX;
    const startY = e.clientY;
    const origField = { ...f };

    const onMove = (ev) => {
      const dx = (ev.clientX - startX) / (cssScaleX * scale);
      const dy = (ev.clientY - startY) / (cssScaleY * scale);
      const newField = { ...f };
      if (handle.includes('n')) newField.y_min = Math.round(origField.y_min + dy);
      if (handle.includes('s')) newField.y_max = Math.round(origField.y_max + dy);
      if (handle.includes('w')) newField.x_min = Math.round(origField.x_min + dx);
      if (handle.includes('e')) newField.x_max = Math.round(origField.x_max + dx);
      if (onHdrResize) onHdrResize(fieldIdx, newField);
    };

    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      resizeRef.current = null;
    };

    resizeRef.current = { onMove, onUp };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  // Resize handler per footer (stesso header)
  const handleFtrResizeStart = (e, fieldIdx, handle) => {
    e.preventDefault();
    e.stopPropagation();
    const fields = template?.footer?.fields;
    if (!fields || fieldIdx >= fields.length) return;
    const f = fields[fieldIdx];
    const startX = e.clientX;
    const startY = e.clientY;
    const origField = { ...f };

    const onMove = (ev) => {
      const dx = (ev.clientX - startX) / (cssScaleX * scale);
      const dy = (ev.clientY - startY) / (cssScaleY * scale);
      const newField = { ...f };
      if (handle.includes('n')) newField.y_min = Math.round(origField.y_min + dy);
      if (handle.includes('s')) newField.y_max = Math.round(origField.y_max + dy);
      if (handle.includes('w')) newField.x_min = Math.round(origField.x_min + dx);
      if (handle.includes('e')) newField.x_max = Math.round(origField.x_max + dx);
      if (onFtrResize) onFtrResize(fieldIdx, newField);
    };

    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  // Resize handler per colonne table (solo X, non Y)
  const handleColResizeStart = (e, colIdx, handle) => {
    e.preventDefault();
    e.stopPropagation();
    const cols = template?.table?.columns;
    if (!cols || colIdx >= cols.length) return;
    const c = cols[colIdx];
    const startX = e.clientX;
    const origCol = { ...c };

    const onMove = (ev) => {
      const dx = (ev.clientX - startX) / (cssScaleX * scale);
      const newCol = { ...c };
      if (handle === 'w') newCol.x_min = Math.round(origCol.x_min + dx);
      if (handle === 'e') newCol.x_max = Math.round(origCol.x_max + dx);
      if (onColResize) onColResize(colIdx, newCol);
    };

    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };

  const zones = [];
  const sections = [];

  // --- Header section ---
  const hdr = template.header;
  if (hdr) {
    const hdrY = toCanvasY(hdr.y_min ?? 0) + offsetY;
    const hdrH = Math.max(Math.abs(toCanvasY(hdr.y_max ?? 200) - toCanvasY(hdr.y_min ?? 0)), 10);
    sections.push({
      key: 'sec-hdr', x: 0, w: 9999, y: hdrY, h: hdrH,
      bg: SECTION_COLORS.header.bg, border: SECTION_COLORS.header.border,
      label: 'HEADER',
    });
    (hdr.fields || []).forEach((f, idx) => {
      const x = toCanvasX(f.x_min ?? 0);
      const y = toCanvasY(f.y_min ?? 0) + offsetY;
      const w = Math.max(Math.abs(toCanvasX(f.x_max ?? 200) - x), 20);
      const h = Math.max(Math.abs(toCanvasY(f.y_max ?? 100) - y), 10);
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
    const tblY = toCanvasY(tbl.y_min ?? 200) + offsetY;
    const tblH = Math.max(Math.abs(toCanvasY(tbl.y_max ?? 600) - toCanvasY(tbl.y_min ?? 200)), 10);
    sections.push({
      key: 'sec-tbl', x: 0, w: 9999, y: tblY, h: tblH,
      bg: SECTION_COLORS.table.bg, border: SECTION_COLORS.table.border,
      label: 'TABLE',
    });
    (tbl.columns || []).forEach((col, idx) => {
      const x = toCanvasX(col.x_min ?? (100 + idx * 80));
      const w = Math.max(Math.abs(toCanvasX(col.x_max ?? (180 + idx * 80)) - x), 30);
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
    const ftrY = toCanvasY(ftr.y_min ?? 600) + offsetY;
    const ftrH = Math.max(Math.abs(toCanvasY(ftr.y_max ?? 800) - toCanvasY(ftr.y_min ?? 600)), 10);
    sections.push({
      key: 'sec-ftr', x: 0, w: 9999, y: ftrY, h: ftrH,
      bg: SECTION_COLORS.footer.bg, border: SECTION_COLORS.footer.border,
      label: 'FOOTER',
    });
    (ftr.fields || []).forEach((f, idx) => {
      const x = toCanvasX(f.x_min ?? 0);
      const y = toCanvasY(f.y_min ?? ftr.y_min) + offsetY;
      const w = Math.max(Math.abs(toCanvasX(f.x_max ?? 200) - x), 20);
      const h = Math.max(Math.abs(toCanvasY(f.y_max ?? ftr.y_max) - toCanvasY(f.y_min ?? ftr.y_min)), 10);
      zones.push({
        key: `f-${idx}`, x, y, w, h,
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
          {zones.map(z => {
            const isHdr = z.key.startsWith('h-');
            const isFtr = z.key.startsWith('f-');
            const isDraggable = isHdr || isFtr;
            const isSelected = (isHdr && parseInt(z.key.split('-')[1], 10) === selectedZoneIndex) || (isFtr && parseInt(z.key.split('-')[1], 10) === selectedFtrZoneIndex);
            return (
            <div
              key={z.key}
              draggable={isDraggable}
              onClick={isDraggable ? (e) => {
                e.stopPropagation();
                const idx = parseInt(z.key.split('-')[1], 10);
                if (isHdr && onZoneClick) onZoneClick(idx);
                if (isFtr && onFtrZoneClick) onFtrZoneClick(idx);
              } : undefined}
              onDragStart={isDraggable ? (e) => {
                const idx = parseInt(z.key.split('-')[1], 10);
                const section = z.key.startsWith('h-') ? 'header' : 'footer';
                e.dataTransfer.setData('application/tag-index', String(idx));
                e.dataTransfer.setData('application/tag-section', section);
                e.dataTransfer.effectAllowed = 'move';
                if (z.w > 0 && z.h > 0) {
                  const ghost = document.createElement('canvas');
                  const s = Math.min(100 / z.w, 1);
                  ghost.width = Math.max(z.w * s, 20);
                  ghost.height = Math.max(z.h * s, 10);
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
                cursor: isDraggable ? 'grab' : 'default',
                fontSize: 10,
                zIndex: 20,
              }}
            >
              <span style={{
                position: 'absolute', top: '-18px', left: 0,
                background: 'rgba(0,0,0,0.85)',
                color: '#fff', padding: '1px 4px',
                whiteSpace: 'nowrap', borderRadius: 2,
                lineHeight: '14px',
              }}>
                {z.label}
              </span>
              {(isHdr && parseInt(z.key.split('-')[1], 10) === selectedZoneIndex) && Object.entries(HANDLE_POSITIONS).map(([hKey, hStyle]) => (
                <div
                  key={hKey}
                  onMouseDown={(e) => {
                    const idx = parseInt(z.key.split('-')[1], 10);
                    handleResizeStart(e, idx, hKey);
                  }}
                  style={{
                    ...RESIZE_HANDLE.style,
                    width: RESIZE_HANDLE.size,
                    height: RESIZE_HANDLE.size,
                    cursor: hStyle.cursor,
                    top: hStyle.top,
                    left: hStyle.left,
                    right: hStyle.right,
                    bottom: hStyle.bottom,
                    marginTop: hStyle.marginTop,
                    marginLeft: hStyle.marginLeft,
                  }}
                />
              ))}
              {/* Maniglie resize per colonne table (solo W e E) */}
              {z.key.startsWith('c-') && ['w', 'e'].map(hKey => {
                const hStyle = HANDLE_POSITIONS[hKey];
                return (
                  <div
                    key={hKey}
                    onMouseDown={(e) => {
                      const idx = parseInt(z.key.split('-')[1], 10);
                      handleColResizeStart(e, idx, hKey);
                    }}
                    style={{
                      ...RESIZE_HANDLE.style,
                      width: RESIZE_HANDLE.size,
                      height: RESIZE_HANDLE.size,
                      cursor: hStyle.cursor,
                      top: hStyle.top,
                      left: hStyle.left,
                      right: hStyle.right,
                      bottom: hStyle.bottom,
                      marginTop: hStyle.marginTop,
                      marginLeft: hStyle.marginLeft,
                    }}
                  />
                );
              })}
              {/* Maniglie resize per footer (stesso comportamento header) */}
              {isFtr && parseInt(z.key.split('-')[1], 10) === selectedFtrZoneIndex && Object.entries(HANDLE_POSITIONS).map(([hKey, hStyle]) => (
                <div
                  key={hKey}
                  onMouseDown={(e) => {
                    const idx = parseInt(z.key.split('-')[1], 10);
                    handleFtrResizeStart(e, idx, hKey);
                  }}
                  style={{
                    ...RESIZE_HANDLE.style,
                    width: RESIZE_HANDLE.size,
                    height: RESIZE_HANDLE.size,
                    cursor: hStyle.cursor,
                    top: hStyle.top,
                    left: hStyle.left,
                    right: hStyle.right,
                    bottom: hStyle.bottom,
                    marginTop: hStyle.marginTop,
                    marginLeft: hStyle.marginLeft,
                  }}
                />
              ))}
            </div>
            );
          })}
        </div>
      )}
    </>
  );
}
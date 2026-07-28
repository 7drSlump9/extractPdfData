import React, { useState, useRef } from 'react';
import { getTemplates, getTemplate, saveTemplate, testPdfTemplate } from '../api';
import PdfViewer from './PdfViewer';
import ErrorBoundary from './ErrorBoundary';

const css = {
  page: { display: 'flex', height: '100vh', background: '#0f172a', color: '#e2e8f0' },
  sidebar: { width: 340, background: '#1e293b', borderRight: '1px solid #334155', overflowY: 'auto', padding: 16, flexShrink: 0 },
  main: { flex: 1, display: 'flex', flexDirection: 'column' },
  topbar: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', background: '#1e293b', borderBottom: '1px solid #334155' },
  dropArea: { flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20, overflow: 'auto' },
  dropBox: { border: '2px dashed #475569', borderRadius: 12, padding: 40, textAlign: 'center', color: '#94a3b8', width: 500, cursor: 'pointer' },
  btn1: { background: '#6366f1', color: '#fff' },
  btn2: { background: '#22c55e', color: '#000' },
  btn3: { background: '#ef4444', color: '#fff' },
  btn4: { background: 'transparent', color: '#94a3b8', border: '1px solid #334155' },
  lbl: { fontSize: 11, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4, marginTop: 10 },
  sec: { background: '#0f172a', borderRadius: 8, padding: 10, marginBottom: 10 },
  row: { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 },
  inp: { flex: 1, minWidth: 0 },
  tag: { fontSize: 10, background: '#334155', padding: '2px 6px', borderRadius: 10, color: '#94a3b8' },
  err: { padding: 8, borderRadius: 6, background: '#7f1d1d', fontSize: 12, color: '#fca5a5' },
  ok: { padding: 8, borderRadius: 6, background: '#064e3b', fontSize: 12, color: '#86efac' },
};

function safeErrMsg(e) {
  return e?.response?.data?.error || e?.message || String(e);
}

function normalizeTemplate(tpl) {
  // Converte dal formato backend (header.fields oggetto, table.columns con name/value)
  // al formato interno (header.fields array, table.columns con header/regex)
  // Gestisce sia nuovo formato (header.fields) che vecchio (header_fields)

  // --- Header ---
  const rawHeader = tpl.header || {};
  let rawHdrFields = rawHeader.fields;
  if (!rawHdrFields) rawHdrFields = tpl.header_fields; // retrocompatibilità

  let hdrFields = [];
  if (Array.isArray(rawHdrFields)) {
    hdrFields = rawHdrFields.map(f => ({
      field: f.field || f.name || '',
      regex: f.regex || f.pattern || '',
      x_min: f.x_min ?? 0,
      y_min: f.y_min ?? 0,
      x_max: f.x_max ?? 0,
      y_max: f.y_max ?? 0,
    }));
  } else if (rawHdrFields && typeof rawHdrFields === 'object') {
    hdrFields = Object.entries(rawHdrFields).map(([key, val]) => ({
      field: key,
      regex: val.pattern || val.regex || '',
      x_min: val.x_min ?? 0,
      y_min: val.y_min ?? 0,
      x_max: val.x_max ?? 0,
      y_max: val.y_max ?? 0,
    }));
  }

  // --- Table ---
  const rawTable = tpl.table || {};
  let cols = [];
  if (Array.isArray(rawTable.columns)) {
    cols = rawTable.columns.map(c => ({
      header: c.header || c.name || '',
      regex: c.regex || c.value || '',
      x_min: c.x_min ?? 0,
      x_max: c.x_max ?? 0,
    }));
  }

  // --- Footer ---
  const rawFooter = tpl.footer || {};
  let rawFtrFields = rawFooter.fields;
  let ftrFields = [];
  if (Array.isArray(rawFtrFields)) {
    ftrFields = rawFtrFields.map(f => ({
      field: f.field || f.name || '',
      regex: f.regex || f.pattern || '',
      x_min: f.x_min ?? 0,
      y_min: f.y_min ?? 0,
      x_max: f.x_max ?? 0,
      y_max: f.y_max ?? 0,
    }));
  } else if (rawFtrFields && typeof rawFtrFields === 'object') {
    ftrFields = Object.entries(rawFtrFields).map(([key, val]) => ({
      field: key,
      regex: val.pattern || val.regex || '',
      x_min: val.x_min ?? 0,
      y_min: val.y_min ?? 0,
      x_max: val.x_max ?? 0,
      y_max: val.y_max ?? 0,
    }));
  }

  return {
    ...tpl,
    header: {
      x_min: rawHeader.x_min ?? 0,
      x_max: rawHeader.x_max ?? 0,
      y_min: rawHeader.y_min ?? 0,
      y_max: rawHeader.y_max ?? 200,
      fields: hdrFields,
    },
    table: {
      ...rawTable,
      y_min: rawTable.y_min ?? 200,
      y_max: rawTable.y_max ?? 600,
      columns: cols,
      end_markers: Array.isArray(rawTable.end_markers) ? rawTable.end_markers : [],
    },
    footer: {
      y_min: rawFooter.y_min ?? 600,
      y_max: rawFooter.y_max ?? 800,
      fields: ftrFields,
    },
    signature: Array.isArray(tpl.signature) ? tpl.signature : [],
  };
}

function denormalizeTemplate(tpl) {
  // Converte dal formato interno al formato backend per salvataggio
  const hdrFieldsObj = {};
  (tpl.header?.fields || []).forEach(f => {
    if (f.field) {
      hdrFieldsObj[f.field] = {
        type: 'regex_full_text',
        pattern: f.regex || '',
        group: 1,
        x_min: f.x_min ?? 0,
        y_min: f.y_min ?? 0,
        x_max: f.x_max ?? 0,
        y_max: f.y_max ?? 0,
      };
    }
  });

  const cols = (tpl.table?.columns || []).map(c => ({
    name: c.header || '',
    x_min: c.x_min ?? 0,
    x_max: c.x_max ?? 0,
    value: c.regex || 'first_word',
  }));

  const ftrFieldsObj = {};
  (tpl.footer?.fields || []).forEach(f => {
    if (f.field) {
      ftrFieldsObj[f.field] = {
        type: 'regex_full_text',
        pattern: f.regex || '',
        group: 1,
        x_min: f.x_min ?? 0,
        y_min: f.y_min ?? 0,
        x_max: f.x_max ?? 0,
        y_max: f.y_max ?? 0,
      };
    }
  });

  return {
    name: tpl.name || '',
    description: tpl.description || '',
    signature: tpl.signature || [],
    customer_file: tpl.customer_file || 'UNKNOWN',
    header: {
      x_min: tpl.header?.x_min ?? 0,
      x_max: tpl.header?.x_max ?? 0,
      y_min: tpl.header?.y_min ?? 0,
      y_max: tpl.header?.y_max ?? 200,
      fields: hdrFieldsObj,
    },
    table: {
      y_min: tpl.table?.y_min ?? 200,
      y_max: tpl.table?.y_max ?? 600,
      layout: tpl.table?.layout || 'rows',
      start_after_contains: tpl.table?.start_after_contains || [],
      end_markers: tpl.table?.end_markers || [],
      row_detect_pattern: tpl.table?.row_detect_pattern || '',
      skip_line_if_matches: tpl.table?.skip_line_if_matches || '',
      columns: cols,
    },
    footer: {
      y_min: tpl.footer?.y_min ?? 600,
      y_max: tpl.footer?.y_max ?? 800,
      fields: ftrFieldsObj,
    },
  };
}

export default function TemplateEditor({ username, onLogout }) {
  const [template, setTemplate] = useState(null);
  const [templateName, setTemplateName] = useState('');
  const [customerName, setCustomerName] = useState('');
  const [pdfFile, setPdfFile] = useState(null);
  const [templates, setTemplates] = useState([]);
  const [message, setMessage] = useState('');
  const [msgOk, setMsgOk] = useState(true);
  const [openHdrIdx, setOpenHdrIdx] = useState(null);
  const pdfRef = useRef(null);
  const tplRef = useRef(null);

  const msg = (text, ok) => { setMessage(text); setMsgOk(!!ok); };

  const loadTemplates = async () => {
    try {
      const list = await getTemplates(customerName || '');
      setTemplates(Array.isArray(list) ? list : []);
      msg(`${(list || []).length} template trovati`, true);
    } catch (e) { msg(safeErrMsg(e), false); }
  };

  const loadFromApi = async (name) => {
    if (!name) return;
    try {
      const tpl = await getTemplate(name, customerName || '');
      if (!tpl || typeof tpl !== 'object') throw new Error('Dati template non validi');
      setTemplate(normalizeTemplate(tpl));
      setTemplateName(tpl.name || name);
      msg(`OK: ${name}`, true);
    } catch (e) { msg(safeErrMsg(e), false); }
  };

  const handleTplFile = (file) => {
    if (!file) return;
    const r = new FileReader();
    r.onload = (ev) => {
      try {
        const tpl = JSON.parse(ev.target.result);
        if (!tpl || typeof tpl !== 'object') throw new Error('JSON non valido');
        setTemplate(normalizeTemplate(tpl));
        setTemplateName(tpl.name || file.name.replace(/\.json$/i, ''));
        msg('Template caricato da file', true);
      } catch (e) { msg(safeErrMsg(e), false); }
    };
    r.onerror = () => msg('Errore lettura file', false);
    r.readAsText(file);
  };

  const handlePdfFile = (file) => {
    if (!file) return;
    if (file.type !== 'application/pdf') { msg('Seleziona un PDF', false); return; }
    setPdfFile(file);
    msg(`PDF: ${file.name}`, true);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    const file = e.dataTransfer?.files?.[0];
    if (!file) return;
    if (file.type === 'application/pdf') handlePdfFile(file);
    else if (file.name.endsWith('.json')) handleTplFile(file);
    else msg('Trascina PDF o JSON', false);
  };

  // --- Header helpers ---
  const addHdr = () => {
    const cur = template?.header?.fields || [];
    const hdr = template?.header || { x_min: 0, x_max: 0, y_min: 0, y_max: 200 };
    setTemplate({ ...template, header: { ...hdr, fields: [...cur, { field: `c${cur.length + 1}`, regex: '', x_min: hdr.x_min ?? 0, y_min: hdr.y_min ?? 0, x_max: hdr.x_max ?? 350, y_max: hdr.y_max ?? 100 }] } });
  };
  const updHdr = (i, k, v) => {
    if (!template?.header?.fields) return;
    const f = [...template.header.fields];
    f[i] = { ...f[i], [k]: v };
    setTemplate({ ...template, header: { ...template.header, fields: f } });
  };
  const delHdr = (i) => {
    const hdr = template?.header || {};
    setTemplate({ ...template, header: { ...hdr, fields: (hdr.fields || []).filter((_, j) => j !== i) } });
  };
  const updHdrSection = (k, v) => {
    if (!template?.header) return;
    setTemplate({ ...template, header: { ...template.header, [k]: Number(v) || 0 } });
  };

  const handleTagDrop = (index, pdfX, pdfY) => {
    const fields = template?.header?.fields;
    if (!fields || index >= fields.length) return;
    const f = fields[index];
    const w = Math.max((f.x_max ?? 0) - (f.x_min ?? 0), 0);
    const h = Math.max((f.y_max ?? 0) - (f.y_min ?? 0), 0);
    const useW = w > 0 ? w : 100;
    const useH = h > 0 ? h : 20;
    const newFields = [...fields];
    newFields[index] = {
      ...newFields[index],
      x_min: pdfX, y_min: pdfY,
      x_max: pdfX + useW, y_max: pdfY + useH,
    };
    setTemplate({ ...template, header: { ...template.header, fields: newFields } });
  };

  // --- Table helpers ---
  const addCol = () => {
    const cur = template?.table?.columns || [];
    const tbl = template?.table || { y_min: 200, y_max: 600 };
    setTemplate({ ...template, table: { ...tbl, columns: [...cur, { header: `c${cur.length + 1}`, regex: '', x_min: 100 + cur.length * 80, x_max: 180 + cur.length * 80 }] } });
  };
  const updCol = (i, k, v) => {
    if (!template?.table?.columns) return;
    const c = [...template.table.columns];
    c[i] = { ...c[i], [k]: v };
    setTemplate({ ...template, table: { ...template.table, columns: c } });
  };
  const delCol = (i) => {
    const tbl = template?.table || {};
    setTemplate({ ...template, table: { ...tbl, columns: (tbl.columns || []).filter((_, j) => j !== i) } });
  };
  const updTableY = (k, v) => {
    if (!template?.table) return;
    setTemplate({ ...template, table: { ...template.table, [k]: Number(v) || 0 } });
  };
  const updTableStr = (k, v) => {
    if (!template?.table) return;
    setTemplate({ ...template, table: { ...template.table, [k]: v } });
  };

  // --- Footer helpers ---
  const addFtr = () => {
    const cur = template?.footer?.fields || [];
    const ftr = template?.footer || { y_min: 600, y_max: 800 };
    setTemplate({ ...template, footer: { ...ftr, fields: [...cur, { field: `f${cur.length + 1}`, regex: '', x_min: 100 + cur.length * 10, y_min: ftr.y_min, x_max: 350, y_max: ftr.y_max }] } });
  };
  const updFtr = (i, k, v) => {
    if (!template?.footer?.fields) return;
    const f = [...template.footer.fields];
    f[i] = { ...f[i], [k]: v };
    setTemplate({ ...template, footer: { ...template.footer, fields: f } });
  };
  const delFtr = (i) => {
    const ftr = template?.footer || {};
    setTemplate({ ...template, footer: { ...ftr, fields: (ftr.fields || []).filter((_, j) => j !== i) } });
  };
  const updFtrY = (k, v) => {
    if (!template?.footer) return;
    setTemplate({ ...template, footer: { ...template.footer, [k]: Number(v) || 0 } });
  };

  const handleSave = async () => {
    if (!template) return msg('Nessun template', false);
    try {
      const toSave = denormalizeTemplate({ ...template, name: templateName || template.name || 'nuovo', customer_file: customerName || 'UNKNOWN' });
      const r = await saveTemplate(toSave);
      msg(`Salvato: ${r?.disk || 'ok'}`, true);
      loadTemplates();
    } catch (e) { msg(safeErrMsg(e), false); }
  };

  const handleTest = async () => {
    if (!template) return msg('Nessun template', false);
    if (!pdfFile) return msg('Carica un PDF prima', false);
    try {
      const toTest = denormalizeTemplate({ ...template, name: templateName || template.name || 'test', customer_file: customerName || 'UNKNOWN' });
      msg('Test in corso...', true);
      const result = await testPdfTemplate(pdfFile, toTest);
      console.log('Test result:', result);
      const righe = result?.righe?.length || 0;
      const campi = Object.keys(result || {}).filter(k => k !== 'formato' && k !== 'righe').length;
      msg(`Test OK: ${campi} campi header, ${righe} righe tabella`, true);
    } catch (e) { msg(safeErrMsg(e), false); }
  };

  const infoMsg = (v) => {
    if (!template) return;
    setTemplate({ ...template, description: v });
  };

  return (
    <ErrorBoundary>
      <div style={css.page}>
        {/* SIDEBAR */}
        <div style={css.sidebar}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <strong style={{ fontSize: 15 }}>Template Editor</strong>
            <button onClick={onLogout} style={{ ...css.btn4, fontSize: 11, padding: '4px 8px' }}>Esci</button>
          </div>

          {/* DB */}
          <div style={css.sec}>
            <div style={css.lbl}>Database</div>
            <div style={{ display: 'flex', gap: 4 }}>
              <input placeholder="customer" value={customerName} onChange={e => setCustomerName(e.target.value)} style={{ flex: 1 }} />
              <button onClick={loadTemplates} style={css.btn1}>Carica</button>
            </div>
            {templates.length > 0 && (
              <select onChange={e => loadFromApi(e.target.value)} style={{ width: '100%', marginTop: 6, fontSize: 12 }} defaultValue="">
                <option value="" disabled>-- template --</option>
                {templates.map(t => <option key={t.name} value={t.name}>{t.name} ({t.customer_name})</option>)}
              </select>
            )}
          </div>

          {/* File */}
          <div style={css.sec}>
            <div style={css.lbl}>File</div>
            <div style={{ display: 'flex', gap: 4 }}>
              <input ref={pdfRef} type="file" accept=".pdf" onChange={e => handlePdfFile(e.target.files[0])} hidden />
              <input ref={tplRef} type="file" accept=".json" onChange={e => handleTplFile(e.target.files[0])} hidden />
              <button onClick={() => pdfRef.current?.click()} style={css.btn4}>PDF</button>
              <button onClick={() => tplRef.current?.click()} style={css.btn4}>Template JSON</button>
            </div>
          </div>

          {message && <div style={msgOk ? css.ok : css.err}>{message}</div>}

          {/* EDITOR */}
          {template && (
            <>
              <div style={css.sec}>
                <div style={css.lbl}>Info</div>
                <div style={{ display: 'flex', gap: 4, marginBottom: 4 }}>
                  <input value={templateName} onChange={e => setTemplateName(e.target.value)} placeholder="Nome" style={{ flex: 1 }} />
                  <button onClick={handleSave} style={css.btn2}>Salva</button>
                  <button onClick={handleTest} style={{ ...css.btn1, fontSize: 11, padding: '4px 8px' }}>Test</button>
                </div>
                <input value={customerName} onChange={e => setCustomerName(e.target.value)} placeholder="Customer" style={{ width: '100%' }} />
              </div>

              {/* === HEADER === */}
              <div style={css.sec}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={css.lbl}>Header ({(template.header?.fields || []).length})</span>
                  <button onClick={addHdr} style={{ ...css.btn1, fontSize: 11, padding: '2px 8px' }}>+</button>
                </div>
                <div style={{ display: 'flex', gap: 4, marginBottom: 4 }}>
                  <span style={{ fontSize: 10, color: '#94a3b8' }}>X:</span>
                  <input type="number" value={template.header?.x_min || 0} onChange={e => updHdrSection('x_min', e.target.value)} placeholder="x_min" style={{ width: 55, fontSize: 10 }} />
                  <input type="number" value={template.header?.x_max || 0} onChange={e => updHdrSection('x_max', e.target.value)} placeholder="x_max" style={{ width: 55, fontSize: 10 }} />
                  <span style={{ fontSize: 10, color: '#94a3b8' }}>Y:</span>
                  <input type="number" value={template.header?.y_min || 0} onChange={e => updHdrSection('y_min', e.target.value)} placeholder="y_min" style={{ width: 55, fontSize: 10 }} />
                  <input type="number" value={template.header?.y_max || 0} onChange={e => updHdrSection('y_max', e.target.value)} placeholder="y_max" style={{ width: 55, fontSize: 10 }} />
                </div>
                {(template.header?.fields || []).map((f, i) => {
                  const isOpen = openHdrIdx === i;
                  return (
                    <div
                      key={i}
                      draggable
                      onDragStart={(e) => {
                        e.dataTransfer.setData('application/tag-index', String(i));
                        e.dataTransfer.effectAllowed = 'move';
                        const w = Math.max((f.x_max ?? 0) - (f.x_min ?? 0), 20);
                        const h = Math.max((f.y_max ?? 0) - (f.y_min ?? 0), 10);
                        const ghost = document.createElement('canvas');
                        const s = Math.min(100 / w, 1);
                        ghost.width = Math.max(w * s, 20);
                        ghost.height = Math.max(h * s, 10);
                        const ctx = ghost.getContext('2d');
                        ctx.fillStyle = 'rgba(59,130,246,0.4)';
                        ctx.fillRect(0, 0, ghost.width, ghost.height);
                        ctx.strokeStyle = '#3b82f6';
                        ctx.lineWidth = 2;
                        ctx.strokeRect(0, 0, ghost.width, ghost.height);
                        e.dataTransfer.setDragImage(ghost, 0, 0);
                      }}
                      style={{ marginBottom: 4, background: '#1e293b', borderRadius: 6, overflow: 'hidden' }}
                    >
                      {/* Header riga compatta */}
                      <div
                        onClick={() => setOpenHdrIdx(isOpen ? null : i)}
                        style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 8px', cursor: 'pointer', userSelect: 'none' }}
                      >
                        <span style={{ fontSize: 10, color: '#94a3b8', transition: 'transform 0.15s', transform: isOpen ? 'rotate(90deg)' : 'rotate(0deg)' }}>
                          ❯
                        </span>
                        <span style={{ fontSize: 12, fontWeight: 600, color: '#e2e8f0', flex: 1 }}>
                          {f.field || `tag ${i + 1}`}
                        </span>
                        <span style={{ fontSize: 10, color: '#64748b', maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {f.regex || '—'}
                        </span>
                        <button
                          onClick={(e) => { e.stopPropagation(); delHdr(i); }}
                          style={{ ...css.btn3, fontSize: 11, padding: '2px 6px', flexShrink: 0 }}
                        >✕</button>
                      </div>
                      {/* Pannello espanso */}
                      {isOpen && (
                        <div style={{ padding: '6px 8px 8px 20px', borderTop: '1px solid #334155' }}>
                          <div style={{ display: 'grid', gridTemplateColumns: '50px 1fr', gap: '4px 8px', fontSize: 11, alignItems: 'center' }}>
                            <span style={{ color: '#94a3b8' }}>Nome</span>
                            <input value={f.field || ''} onChange={e => updHdr(i, 'field', e.target.value)} placeholder="nome campo" style={{ fontSize: 11, width: '100%' }} />
                            <span style={{ color: '#94a3b8' }}>Pattern</span>
                            <input value={f.regex || ''} onChange={e => updHdr(i, 'regex', e.target.value)} placeholder="regex pattern" style={{ fontSize: 11, width: '100%' }} />
                            <span style={{ color: '#94a3b8' }}>X</span>
                            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                              <input type="number" value={f.x_min ?? 0} onChange={e => updHdr(i, 'x_min', Number(e.target.value))} placeholder="x0" style={{ width: 55, fontSize: 10 }} />
                              <span style={{ color: '#64748b', fontSize: 10 }}>→</span>
                              <input type="number" value={f.x_max ?? 0} onChange={e => updHdr(i, 'x_max', Number(e.target.value))} placeholder="x1" style={{ width: 55, fontSize: 10 }} />
                            </div>
                            <span style={{ color: '#94a3b8' }}>Y</span>
                            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                              <input type="number" value={f.y_min ?? 0} onChange={e => updHdr(i, 'y_min', Number(e.target.value))} placeholder="y0" style={{ width: 55, fontSize: 10 }} />
                              <span style={{ color: '#64748b', fontSize: 10 }}>→</span>
                              <input type="number" value={f.y_max ?? 0} onChange={e => updHdr(i, 'y_max', Number(e.target.value))} placeholder="y1" style={{ width: 55, fontSize: 10 }} />
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* === TABLE === */}
              <div style={css.sec}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={css.lbl}>Table ({(template.table?.columns || []).length})</span>
                  <button onClick={addCol} style={{ ...css.btn1, fontSize: 11, padding: '2px 8px' }}>+</button>
                </div>
                <div style={{ display: 'flex', gap: 4, marginBottom: 4 }}>
                  <span style={{ fontSize: 10, color: '#94a3b8' }}>Y:</span>
                  <input type="number" value={template.table?.y_min || 0} onChange={e => updTableY('y_min', e.target.value)} placeholder="y_min" style={{ width: 55, fontSize: 10 }} />
                  <input type="number" value={template.table?.y_max || 0} onChange={e => updTableY('y_max', e.target.value)} placeholder="y_max" style={{ width: 55, fontSize: 10 }} />
                </div>
                {(template.table?.columns || []).map((c, i) => (
                  <div key={i} style={css.row}>
                    <input value={c.header || ''} onChange={e => updCol(i, 'header', e.target.value)} placeholder="hdr" style={{ ...css.inp, maxWidth: 55 }} />
                    <input value={c.regex || ''} onChange={e => updCol(i, 'regex', e.target.value)} placeholder="regex" style={{ ...css.inp, maxWidth: 70 }} />
                    <span style={css.tag}>x{Number(c.x_min)?.toFixed(0)}-{Number(c.x_max)?.toFixed(0)}</span>
                    <button onClick={() => delCol(i)} style={{ ...css.btn3, fontSize: 11, padding: '2px 6px' }}>x</button>
                  </div>
                ))}
                <input value={template.table?.start_after_contains || ''} onChange={e => updTableStr('start_after_contains', e.target.value)} placeholder="Start after (virgola)" style={{ width: '100%', marginTop: 4, fontSize: 11 }} />
                <input value={(template.table?.end_markers || []).join(', ')} onChange={e => updTableStr('end_markers', e.target.value.split(',').map(s => s.trim()))} placeholder="End markers (virgola)" style={{ width: '100%', marginTop: 2, fontSize: 11 }} />
                <input value={template.table?.row_detect_pattern || ''} onChange={e => updTableStr('row_detect_pattern', e.target.value)} placeholder="Row detect regex" style={{ width: '100%', marginTop: 2, fontSize: 11 }} />
                <input value={template.table?.skip_line_if_matches || ''} onChange={e => updTableStr('skip_line_if_matches', e.target.value)} placeholder="Skip line regex" style={{ width: '100%', marginTop: 2, fontSize: 11 }} />
              </div>

              {/* === FOOTER === */}
              <div style={css.sec}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={css.lbl}>Footer ({(template.footer?.fields || []).length})</span>
                  <button onClick={addFtr} style={{ ...css.btn1, fontSize: 11, padding: '2px 8px' }}>+</button>
                </div>
                <div style={{ display: 'flex', gap: 4, marginBottom: 4 }}>
                  <span style={{ fontSize: 10, color: '#94a3b8' }}>Y:</span>
                  <input type="number" value={template.footer?.y_min || 0} onChange={e => updFtrY('y_min', e.target.value)} placeholder="y_min" style={{ width: 55, fontSize: 10 }} />
                  <input type="number" value={template.footer?.y_max || 0} onChange={e => updFtrY('y_max', e.target.value)} placeholder="y_max" style={{ width: 55, fontSize: 10 }} />
                </div>
                {(template.footer?.fields || []).map((f, i) => (
                  <div key={i} style={css.row}>
                    <input value={f.field || ''} onChange={e => updFtr(i, 'field', e.target.value)} placeholder="nome" style={{ ...css.inp, maxWidth: 70 }} />
                    <input value={f.regex || ''} onChange={e => updFtr(i, 'regex', e.target.value)} placeholder="regex" style={{ ...css.inp, maxWidth: 90 }} />
                    <input type="number" value={f.x_min ?? 0} onChange={e => updFtr(i, 'x_min', Number(e.target.value))} placeholder="x0" style={{ width: 40, fontSize: 10 }} />
                    <input type="number" value={f.x_max ?? 0} onChange={e => updFtr(i, 'x_max', Number(e.target.value))} placeholder="x1" style={{ width: 40, fontSize: 10 }} />
                    <button onClick={() => delFtr(i)} style={{ ...css.btn3, fontSize: 11, padding: '2px 6px' }}>x</button>
                  </div>
                ))}
              </div>

              {/* Signature */}
              <div style={css.sec}>
                <span style={css.lbl}>Signature</span>
                <input value={(template.signature || []).join(', ')} onChange={e => setTemplate({ ...template, signature: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} placeholder="sig1, sig2" style={{ width: '100%', fontSize: 11 }} />
              </div>

              {/* Riepilogo */}
              <div style={css.sec}>
                <div style={css.lbl}>Riepilogo</div>
                <pre style={{
                  margin: 0, padding: 8, background: '#0f172a', borderRadius: 6,
                  fontSize: 10, fontFamily: 'monospace', color: '#94a3b8',
                  maxHeight: 250, overflowY: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  lineHeight: 1.5
                }}>
{`Template: ${templateName || template.name || '-'}
Customer: ${customerName || '-'}

HEADER [Y:${template.header?.y_min || 0}-${template.header?.y_max || 0}] (${(template.header?.fields || []).length} fields):
${(template.header?.fields || []).map(f => `  ${f.field}: ${f.regex || '-'}  [${Number(f.x_min)?.toFixed(0)},${Number(f.y_min)?.toFixed(0)} ${Number(f.x_max)?.toFixed(0)}x${Number(f.y_max)?.toFixed(0)}]`).join('\n') || '  (nessuno)'}

TABLE [Y:${template.table?.y_min || 0}-${template.table?.y_max || 0}] (${(template.table?.columns || []).length} cols):
${(template.table?.columns || []).map(c => `  ${c.header}: ${c.regex || '-'}  [x${Number(c.x_min)?.toFixed(0)}-${Number(c.x_max)?.toFixed(0)}]`).join('\n') || '  (nessuno)'}
  start_after: [${(template.table?.start_after_contains || '').toString() || '-'}]
  end_markers: [${(template.table?.end_markers || []).map(m => `"${m}"`).join(', ') || '-'}]
  row_detect: ${template.table?.row_detect_pattern || '-'}
  skip: ${template.table?.skip_line_if_matches || '-'}

FOOTER [Y:${template.footer?.y_min || 0}-${template.footer?.y_max || 0}] (${(template.footer?.fields || []).length} fields):
${(template.footer?.fields || []).map(f => `  ${f.field}: ${f.regex || '-'}  [${Number(f.x_min)?.toFixed(0)},${Number(f.y_min)?.toFixed(0)} ${Number(f.x_max)?.toFixed(0)}x${Number(f.y_max)?.toFixed(0)}]`).join('\n') || '  (nessuno)'}

Signature: ${(template.signature || []).join(', ') || '-'}`}
                </pre>
              </div>
            </>
          )}
        </div>

        {/* MAIN */}
        <div style={css.main}>
          <div style={css.topbar}>
            <span>{template ? `${templateName || template.name || 'senza nome'}` : 'Nessun template'}</span>
            <span style={{ fontSize: 12, color: '#94a3b8' }}>{username} {pdfFile ? `| ${pdfFile.name}` : ''}</span>
          </div>
          <div style={css.dropArea} onDragOver={e => e.preventDefault()} onDrop={handleDrop}>
            {pdfFile ? (
              <PdfViewer file={pdfFile} template={template} onTagDrop={handleTagDrop} />
            ) : (
              <div style={css.dropBox}>
                <div style={{ fontSize: 40, marginBottom: 8 }}>.</div>
                <div>Trascina un PDF qui</div>
                <button onClick={() => pdfRef.current?.click()} style={{ ...css.btn1, marginTop: 12 }}>Scegli PDF</button>
              </div>
            )}
          </div>
        </div>
      </div>
    </ErrorBoundary>
  );
}
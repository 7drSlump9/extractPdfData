import React, { useState, useRef } from 'react';
import { getTemplates, getTemplate, saveTemplate } from '../api';
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

export default function TemplateEditor({ username, onLogout }) {
  const [template, setTemplate] = useState(null);
  const [templateName, setTemplateName] = useState('');
  const [customerName, setCustomerName] = useState('');
  const [pdfFile, setPdfFile] = useState(null);
  const [templates, setTemplates] = useState([]);
  const [message, setMessage] = useState('');
  const [msgOk, setMsgOk] = useState(true);
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
      setTemplate(tpl);
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
        setTemplate(tpl);
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

  const addHdr = () => {
    const cur = template?.header_fields || [];
    setTemplate({ ...(template || {}), header_fields: [...cur, { field: `c${cur.length + 1}`, regex: '', x_min: 100 + cur.length * 10, y_min: 400, x_max: 350, y_max: 430 }] });
  };
  const updHdr = (i, k, v) => {
    if (!template?.header_fields) return;
    const h = [...template.header_fields];
    h[i] = { ...h[i], [k]: v };
    setTemplate({ ...template, header_fields: h });
  };
  const delHdr = (i) => {
    setTemplate({ ...template, header_fields: (template.header_fields || []).filter((_, j) => j !== i) });
  };

  const addCol = () => {
    const cur = template?.table?.columns || [];
    setTemplate({ ...(template || {}), table: { ...(template?.table || {}), columns: [...cur, { header: `c${cur.length + 1}`, regex: '', x_min: 100 + cur.length * 80, x_max: 180 + cur.length * 80 }] } });
  };
  const updCol = (i, k, v) => {
    if (!template?.table?.columns) return;
    const c = [...template.table.columns];
    c[i] = { ...c[i], [k]: v };
    setTemplate({ ...template, table: { ...template.table, columns: c } });
  };
  const delCol = (i) => {
    const c = (template.table.columns || []).filter((_, j) => j !== i);
    setTemplate({ ...template, table: { ...template.table, columns: c } });
  };

  const handleSave = async () => {
    if (!template) return msg('Nessun template', false);
    try {
      const toSave = { ...template, name: templateName || template.name || 'nuovo', customer_file: customerName || 'UNKNOWN' };
      const r = await saveTemplate(toSave);
      msg(`Salvato: ${r?.disk || 'ok'}`, true);
      loadTemplates();
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
                </div>
                <input value={customerName} onChange={e => setCustomerName(e.target.value)} placeholder="Customer" style={{ width: '100%' }} />
              </div>

              {/* Header */}
              <div style={css.sec}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={css.lbl}>Header ({template.header_fields?.length || 0})</span>
                  <button onClick={addHdr} style={{ ...css.btn1, fontSize: 11, padding: '2px 8px' }}>+</button>
                </div>
                {(template.header_fields || []).map((f, i) => (
                  <div key={i} style={css.row}>
                    <input value={f.field || ''} onChange={e => updHdr(i, 'field', e.target.value)} placeholder="nome" style={{ ...css.inp, maxWidth: 80 }} />
                    <input value={f.regex || ''} onChange={e => updHdr(i, 'regex', e.target.value)} placeholder="regex" style={{ ...css.inp, maxWidth: 100 }} />
                    <span style={css.tag}>{Number(f.x_min)?.toFixed(0)},{Number(f.y_min)?.toFixed(0)}</span>
                    <button onClick={() => delHdr(i)} style={{ ...css.btn3, fontSize: 11, padding: '2px 6px' }}>x</button>
                  </div>
                ))}
              </div>

              {/* Table */}
              <div style={css.sec}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={css.lbl}>Table ({template.table?.columns?.length || 0})</span>
                  <button onClick={addCol} style={{ ...css.btn1, fontSize: 11, padding: '2px 8px' }}>+</button>
                </div>
                {(template.table?.columns || []).map((c, i) => (
                  <div key={i} style={css.row}>
                    <input value={c.header || ''} onChange={e => updCol(i, 'header', e.target.value)} placeholder="hdr" style={{ ...css.inp, maxWidth: 60 }} />
                    <input value={c.regex || ''} onChange={e => updCol(i, 'regex', e.target.value)} placeholder="regex" style={{ ...css.inp, maxWidth: 80 }} />
                    <span style={css.tag}>x{Number(c.x_min)?.toFixed(0)}-{Number(c.x_max)?.toFixed(0)}</span>
                    <button onClick={() => delCol(i)} style={{ ...css.btn3, fontSize: 11, padding: '2px 6px' }}>x</button>
                  </div>
                ))}
                <input value={template.table?.start_after_contains || ''} onChange={e => { const t = template; t.table = { ...t.table, start_after_contains: e.target.value }; setTemplate({ ...t }); }} placeholder="Start after..." style={{ width: '100%', marginTop: 4, fontSize: 11 }} />
                <input value={(template.table?.end_markers || []).join(', ')} onChange={e => { const t = template; t.table = { ...t.table, end_markers: e.target.value.split(',').map(s => s.trim()) }; setTemplate({ ...t }); }} placeholder="End markers" style={{ width: '100%', marginTop: 2, fontSize: 11 }} />
              </div>

              {/* Signature */}
              <div style={css.sec}>
                <span style={css.lbl}>Signature</span>
                <input value={(template.signature || []).join(', ')} onChange={e => setTemplate({ ...template, signature: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} placeholder="sig1, sig2" style={{ width: '100%', fontSize: 11 }} />
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
              <PdfViewer file={pdfFile} template={template} />
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
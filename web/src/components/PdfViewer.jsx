import React, { useEffect, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import ZoneOverlay from './ZoneOverlay';

pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.9.155/pdf.worker.min.mjs';

export default function PdfViewer({ file, template }) {
  const canvasRef = useRef(null);
  const pdfDocRef = useRef(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [numPages, setNumPages] = useState(0);
  const [scale, setScale] = useState(1.5);
  const [loading, setLoading] = useState(false);
  const renderIdRef = useRef(0);

  // Carica PDF quando il file cambia
  useEffect(() => {
    if (!file) return;
    setLoading(true);
    pdfDocRef.current = null;
    const reader = new FileReader();
    reader.onload = async (e) => {
      const doc = await pdfjsLib.getDocument({ data: e.target.result }).promise;
      pdfDocRef.current = doc;
      setNumPages(doc.numPages);
      setCurrentPage(1);
      setLoading(false);
      renderPage(doc, 1, scale, ++renderIdRef.current);
    };
    reader.readAsArrayBuffer(file);
  }, [file]);

  // Render quando cambia pagina o zoom
  useEffect(() => {
    if (pdfDocRef.current && currentPage > 0) {
      renderPage(pdfDocRef.current, currentPage, scale, ++renderIdRef.current);
    }
  }, [currentPage, scale]);

  const renderPage = async (doc, pageNum, scl, renderId) => {
    setLoading(true);
    try {
      const page = await doc.getPage(pageNum);
      const viewport = page.getViewport({ scale: scl });
      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      const ctx = canvas.getContext('2d');
      if (renderIdRef.current !== renderId) return; // abort se nuovo render
      await page.render({ canvasContext: ctx, viewport }).promise;
      if (renderIdRef.current !== renderId) return;
      setLoading(false);
    } catch (err) {
      console.error('Render error:', err);
      setLoading(false);
    }
  };

  const prevPage = () => setCurrentPage(p => Math.max(1, p - 1));
  const nextPage = () => setCurrentPage(p => Math.min(numPages, p + 1));

  return (
    <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%' }}>
      {/* Toolbar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, padding: '6px 10px', background: '#1e293b', borderRadius: 8 }}>
        <button onClick={prevPage} disabled={currentPage <= 1} style={{ background: currentPage <= 1 ? '#334155' : '#475569', color: '#fff', padding: '4px 10px', borderRadius: 4 }}>◀</button>
        <span style={{ fontSize: 13, color: '#e2e8f0' }}>Pagina {currentPage} / {numPages || '?'}</span>
        <button onClick={nextPage} disabled={currentPage >= numPages} style={{ background: currentPage >= numPages ? '#334155' : '#475569', color: '#fff', padding: '4px 10px', borderRadius: 4 }}>▶</button>
        <input type="range" min="0.5" max="3" step="0.1" value={scale} onChange={(e) => setScale(Number(e.target.value))} style={{ width: 80 }} />
        <span style={{ fontSize: 12, color: '#94a3b8' }}>{scale}x</span>
      </div>

      {/* Canvas + overlay */}
      <div style={{ position: 'relative', display: 'inline-block', borderRadius: 6, overflow: 'hidden', boxShadow: '0 4px 24px rgba(0,0,0,0.4)' }}>
        {loading && <div style={{ position: 'absolute', top: 10, left: 10, background: 'rgba(0,0,0,0.6)', color: '#fff', padding: '4px 10px', borderRadius: 4, fontSize: 12, zIndex: 30 }}>Caricamento...</div>}
        <canvas ref={canvasRef} style={{ display: 'block', maxWidth: '100%' }} />
        <ZoneOverlay template={template} scale={scale} />
      </div>
    </div>
  );
}
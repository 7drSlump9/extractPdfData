import React, { useEffect, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import ZoneOverlay from './ZoneOverlay';

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker;

export default function PdfViewer({ file, template, onTagDrop, onHdrResize, onColResize, onFtrDrop, onFtrResize, onZoneClick, selectedZoneIndex, onFtrZoneClick, selectedFtrZoneIndex }) {
  const canvasRef = useRef(null);
  const pdfDocRef = useRef(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [numPages, setNumPages] = useState(0);
  const [scale, setScale] = useState(1.5);
  const [yOffset, setYOffset] = useState(0);
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

  const [canvasScaleX, setCanvasScaleX] = useState(1);
  const [canvasScaleY, setCanvasScaleY] = useState(1);

  // Aggiorna il rapporto di scala CSS quando il canvas viene ridimensionato
  useEffect(() => {
    const observer = new ResizeObserver(() => {
      const canvas = canvasRef.current;
      if (canvas) {
        const rect = canvas.getBoundingClientRect();
        setCanvasScaleX(rect.width > 0 ? canvas.width / rect.width : 1);
        setCanvasScaleY(rect.height > 0 ? canvas.height / rect.height : 1);
      }
    });
    const canvas = canvasRef.current;
    if (canvas) observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

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
        <span style={{ fontSize: 10, color: '#94a3b8', marginLeft: 8 }}>Y:</span>
        <input type="number" value={yOffset} onChange={(e) => setYOffset(Number(e.target.value) || 0)} style={{ width: 50, fontSize: 11 }} />
      </div>

      {/* Canvas + overlay */}
      <div
        style={{ position: 'relative', display: 'inline-block', borderRadius: 6, overflow: 'visible', boxShadow: '0 4px 24px rgba(0,0,0,0.4)' }}
      >
        {loading && <div style={{ position: 'absolute', top: 10, left: 10, background: 'rgba(0,0,0,0.6)', color: '#fff', padding: '4px 10px', borderRadius: 4, fontSize: 12, zIndex: 30 }}>Caricamento...</div>}
        <canvas
          ref={canvasRef}
          style={{ display: 'block', maxWidth: '100%', position: 'relative', zIndex: 1 }}
          onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }}
          onDrop={(e) => {
            e.preventDefault();
            const raw = e.dataTransfer.getData('application/tag-index');
            if (raw === '') return;
            const index = parseInt(raw, 10);
            if (isNaN(index)) return;
            const section = e.dataTransfer.getData('application/tag-section');
            const canvas = canvasRef.current;
            if (!canvas) return;
            const rect = canvas.getBoundingClientRect();
            const scaleX = canvas.width / rect.width;
            const scaleY = canvas.height / rect.height;
            const canvasX = (e.clientX - rect.left) * scaleX;
            const canvasY = (e.clientY - rect.top) * scaleY;
            // Converti in coordinate 0-1000 (permille)
            const pdfX = Math.round((canvasX / canvas.width) * 1000);
            const pdfY = Math.round((canvasY / canvas.height) * 1000) - yOffset;
            if (section === 'footer' && onFtrDrop) {
              onFtrDrop(index, pdfX, pdfY);
            } else if (onTagDrop) {
              onTagDrop(index, pdfX, pdfY);
            }
          }}
        />
        <ZoneOverlay template={template} scale={scale} yOffset={yOffset} canvasScaleX={canvasScaleX} canvasScaleY={canvasScaleY} canvasRefWidth={canvasRef.current?.width || 0} canvasRefHeight={canvasRef.current?.height || 0} onHdrResize={onHdrResize} onColResize={onColResize} onFtrResize={onFtrResize} onZoneClick={onZoneClick} selectedZoneIndex={selectedZoneIndex} onFtrZoneClick={onFtrZoneClick} selectedFtrZoneIndex={selectedFtrZoneIndex} />
      </div>
    </div>
  );
}
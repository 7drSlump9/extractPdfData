import React, { Component } from 'react';

export default class ErrorBoundary extends Component {
  state = { error: null, stack: '' };

  static getDerivedStateFromError(error) {
    return { error, stack: error.stack || '' };
  }

  componentDidCatch(error, info) {
    console.error('ErrorBoundary caught:', error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 40, color: '#fca5a5', background: '#1e293b', borderRadius: 8, margin: 20 }}>
          <h3 style={{ color: '#ef4444', marginBottom: 10 }}>Errore applicazione</h3>
          <pre style={{ fontSize: 12, whiteSpace: 'pre-wrap', maxHeight: 300, overflow: 'auto' }}>
            {this.state.error?.message || String(this.state.error)}
          </pre>
          <button
            onClick={() => { this.setState({ error: null }); window.location.reload(); }}
            style={{ marginTop: 16, background: '#6366f1', color: '#fff', padding: '8px 20px', borderRadius: 6 }}
          >
            Ricarica
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
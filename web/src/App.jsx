import React, { useState, useEffect } from 'react';
import { whoami, logout } from './api';
import Login from './components/Login';
import TemplateEditor from './components/TemplateEditor';

export default function App() {
  const [loggedIn, setLoggedIn] = useState(false);
  const [username, setUsername] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    whoami().then(r => {
      if (r.logged_in) { setLoggedIn(true); setUsername(r.username); }
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', color: '#94a3b8' }}>Caricamento...</div>;
  if (!loggedIn) return <Login onLogin={(u) => { setLoggedIn(true); setUsername(u); }} />;

  return (
    <TemplateEditor username={username} onLogout={async () => { await logout(); setLoggedIn(false); }} />
  );
}
import axios from 'axios';

const api = axios.create({ baseURL: '/api', withCredentials: true });

export async function login(username, password) {
  const { data } = await api.post('/login', { username, password });
  return data;
}

export async function logout() {
  const { data } = await api.post('/logout');
  return data;
}

export async function whoami() {
  const { data } = await api.get('/whoami');
  return data;
}

export async function getTemplates(customerName) {
  const params = customerName ? { customer_name: customerName } : {};
  const { data } = await api.get('/templates', { params });
  return data;
}

export async function getTemplate(name, customerName) {
  const params = customerName ? { customer_name: customerName } : {};
  const { data } = await api.get(`/templates/${encodeURIComponent(name)}`, { params });
  return data;
}

export async function saveTemplate(template) {
  const { data } = await api.post('/templates', template);
  return data;
}

export async function testTemplate({ template, lines, fullText }) {
  const { data } = await api.post('/test-template', { template, lines, full_text: fullText });
  return data;
}

export async function testPdfTemplate(pdfFile, template) {
  const formData = new FormData();
  formData.append('file', pdfFile);
  formData.append('template', JSON.stringify(template));
  const { data } = await api.post('/test-pdf', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return data;
}

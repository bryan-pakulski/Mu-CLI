// --- networking helpers -----------------------------------------------------
async function api(path, method='GET', body=null) {
  const res = await fetch(path, {
    method,
    headers: {'Content-Type': 'application/json'},
    body: body ? JSON.stringify(body) : null,
  });
  const json = await parseJsonResponse(res);
  if (!res.ok) throw new Error(json.error || 'request failed');
  return json;
}

async function apiForm(path, formData) {
  const res = await fetch(path, { method: 'POST', body: formData });
  const json = await parseJsonResponse(res);
  if (!res.ok) throw new Error(json.error || 'request failed');
  return json;
}

async function parseJsonResponse(res) {
  const contentType = String(res.headers.get('content-type') || '').toLowerCase();
  if (contentType.includes('application/json')) return res.json();
  const raw = await res.text();
  if (!raw.trim()) return {};
  try {
    return JSON.parse(raw);
  } catch (_) {
    return { error: raw };
  }
}

const API_ROOT = '/api/v1'

async function request(path, options = {}) {
  const response = await fetch(`${API_ROOT}${path}`, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(payload.detail || '请求失败，请稍后重试。')
  }
  return payload
}

export function uploadTask(file, options) {
  const form = new FormData()
  form.append('file', file)
  form.append('report_mode', options.report_mode)
  form.append('verify_entities', String(options.verify_entities))
  return request('/tasks/upload', { method: 'POST', body: form })
}

export function createUrlTask(url, options) {
  return request('/tasks/url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, ...options }),
  })
}

export function getTask(taskId) {
  return request(`/tasks/${taskId}`)
}

export function getTasks() {
  return request('/tasks')
}

export function getReport(taskId) {
  return request(`/tasks/${taskId}/report`)
}

export function cancelTask(taskId) {
  return request(`/tasks/${taskId}/cancel`, { method: 'POST' })
}

export function assetUrl(taskId, relativePath) {
  return `${API_ROOT}/tasks/${taskId}/assets/${relativePath
    .split('/')
    .map(encodeURIComponent)
    .join('/')}`
}

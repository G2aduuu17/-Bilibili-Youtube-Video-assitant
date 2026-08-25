import { useCallback, useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  assetUrl,
  cancelTask,
  createUrlTask,
  getReport,
  getTask,
  getTasks,
  uploadTask,
} from './api'

const stageNames = {
  queued: '等待队列',
  acquire: '获取视频',
  probe: '读取视频信息',
  audio: '提取音频',
  asr: '语音识别',
  frames: '抽取关键画面',
  segments: '划分片段',
  multimodal_analysis: '多模态分析',
  entity_verification: '专名核验',
  entity_corrections: '应用修正',
  summary: '综合分析',
  report: '生成报告',
  completed: '已完成',
  failed: '处理失败',
  cancelled: '已取消',
  cancelling: '正在取消',
}

function formatDate(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}

function TaskComposer({ onTaskCreated }) {
  const [mode, setMode] = useState('upload')
  const [file, setFile] = useState(null)
  const [url, setUrl] = useState('')
  const [reportMode, setReportMode] = useState('detailed')
  const [verifyEntities, setVerifyEntities] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  async function submit(event) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const task = mode === 'upload'
        ? await uploadTask(file, {
          report_mode: reportMode,
          verify_entities: reportMode === 'fast' ? false : verifyEntities,
        })
        : await createUrlTask(url, {
          report_mode: reportMode,
          verify_entities: reportMode === 'fast' ? false : verifyEntities,
        })
      onTaskCreated(task)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setSubmitting(false)
    }
  }

  const canSubmit = mode === 'upload' ? Boolean(file) : Boolean(url.trim())

  return (
    <form className="composer" onSubmit={submit}>
      <div className="mode-switch" role="tablist" aria-label="视频来源">
        <button type="button" className={mode === 'upload' ? 'active' : ''} onClick={() => setMode('upload')}>
          本地视频
        </button>
        <button type="button" className={mode === 'url' ? 'active' : ''} onClick={() => setMode('url')}>
          视频链接
        </button>
      </div>

      {mode === 'upload' ? (
        <label className="drop-zone">
          <input
            type="file"
            accept="video/mp4,video/quicktime,video/x-matroska,video/webm,video/x-msvideo"
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
          <span className="drop-icon">↑</span>
          <strong>{file ? file.name : '选择或拖入视频文件'}</strong>
          <small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : '支持 MP4、MOV、MKV、WebM、AVI、M4V'}</small>
        </label>
      ) : (
        <label className="url-field">
          <span>视频地址</span>
          <input
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="粘贴 B 站或其他受支持的视频链接"
            required
          />
        </label>
      )}

      <div className="options-row">
        <label>
          <span>报告深度</span>
          <select value={reportMode} onChange={(event) => {
            const nextMode = event.target.value
            setReportMode(nextMode)
            if (nextMode === 'fast') setVerifyEntities(false)
          }}>
            <option value="fast">极速摘要（字幕优先）</option>
            <option value="standard">标准报告</option>
            <option value="detailed">深度报告</option>
          </select>
        </label>
        <label className="check-label">
          <input type="checkbox" checked={verifyEntities} disabled={reportMode === 'fast'} onChange={(event) => setVerifyEntities(event.target.checked)} />
          核验专名和关键词
        </label>
      </div>

      {error && <p className="form-error">{error}</p>}
      <button className="primary-button" disabled={!canSubmit || submitting}>
        {submitting ? '正在创建任务…' : '开始生成报告'}
      </button>
    </form>
  )
}

function ProgressPanel({ task, onCancel }) {
  const terminal = ['completed', 'failed', 'cancelled'].includes(task.status)
  return (
    <section className="progress-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">当前任务</p>
          <h2>{task.filename || task.source_url || '在线视频分析'}</h2>
        </div>
        <span className={`status ${task.status}`}>{stageNames[task.status] || task.status}</span>
      </div>
      <div className="progress-meta">
        <span>{stageNames[task.progress.stage] || task.progress.stage}</span>
        <strong>{task.progress.percent}%</strong>
      </div>
      <div className="progress-track"><span style={{ width: `${task.progress.percent}%` }} /></div>
      <p className="progress-message">{task.progress.message}</p>
      {task.error && <p className="form-error">{task.error}</p>}
      {!terminal && <button className="quiet-button" onClick={onCancel}>取消任务</button>}
    </section>
  )
}

function ReportView({ taskId, report }) {
  const components = useMemo(() => ({
    img: ({ src = '', alt = '' }) => {
      const imageSource = /^https?:\/\//.test(src) ? src : assetUrl(taskId, src)
      return <img src={imageSource} alt={alt} loading="lazy" />
    },
    a: ({ href = '', children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a>,
  }), [taskId])

  return (
    <section className="report-card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">已完成</p>
          <h2>{report.title}</h2>
        </div>
        <a className="quiet-button" href={`data:text/markdown;charset=utf-8,${encodeURIComponent(report.markdown)}`} download="video-report.md">
          下载 Markdown
        </a>
      </div>
      <article className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{report.markdown}</ReactMarkdown></article>
    </section>
  )
}

function History({ tasks, activeTaskId, onSelect }) {
  return (
    <aside className="history">
      <div className="history-title"><span>历史任务</span><span>{tasks.length}</span></div>
      {tasks.length === 0 ? <p className="empty">创建第一个视频分析任务后，它会出现在这里。</p> : (
        <div className="history-list">
          {tasks.map((task) => (
            <button key={task.task_id} className={task.task_id === activeTaskId ? 'history-item active' : 'history-item'} onClick={() => onSelect(task)}>
              <span className="history-name">{task.filename || task.source_url || '未命名任务'}</span>
              <span>{stageNames[task.status] || task.status} · {formatDate(task.created_at)}</span>
            </button>
          ))}
        </div>
      )}
    </aside>
  )
}

export default function App() {
  const [currentTask, setCurrentTask] = useState(null)
  const [report, setReport] = useState(null)
  const [tasks, setTasks] = useState([])
  const [loadError, setLoadError] = useState('')

  const refreshHistory = useCallback(async () => {
    try {
      const data = await getTasks()
      setTasks(data.items)
    } catch (error) {
      setLoadError(error.message)
    }
  }, [])

  const loadReport = useCallback(async (taskId) => {
    try {
      setReport(await getReport(taskId))
    } catch (error) {
      setLoadError(error.message)
    }
  }, [])

  useEffect(() => { refreshHistory() }, [refreshHistory])

  useEffect(() => {
    if (!currentTask || ['completed', 'failed', 'cancelled'].includes(currentTask.status)) {
      if (currentTask?.status === 'completed') loadReport(currentTask.task_id)
      return undefined
    }
    const timer = window.setInterval(async () => {
      try {
        const updated = await getTask(currentTask.task_id)
        setCurrentTask(updated)
        if (updated.status === 'completed') {
          await loadReport(updated.task_id)
          await refreshHistory()
        }
      } catch (error) {
        setLoadError(error.message)
      }
    }, 2000)
    return () => window.clearInterval(timer)
  }, [currentTask, loadReport, refreshHistory])

  async function selectTask(task) {
    setCurrentTask(task)
    setReport(null)
    if (task.status === 'completed') await loadReport(task.task_id)
  }

  async function onCancel() {
    if (!currentTask) return
    try {
      setCurrentTask(await cancelTask(currentTask.task_id))
      await refreshHistory()
    } catch (error) {
      setLoadError(error.message)
    }
  }

  function onTaskCreated(task) {
    setCurrentTask(task)
    setReport(null)
    setTasks((previous) => [task, ...previous.filter((item) => item.task_id !== task.task_id)])
  }

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="/">Video<span>Devour</span></a>
        <p>把视频沉淀为结构化、多模态、可回溯的报告</p>
      </header>
      <div className="shell">
        <section className="hero">
          <p className="eyebrow">MULTIMODAL VIDEO RESEARCH</p>
          <h1>让每一段视频<br /><em>变成可用知识。</em></h1>
          <p className="hero-copy">上传文件或提交链接。系统将提取语音、画面与关键证据，并生成可阅读、可编辑的深度报告。</p>
          <TaskComposer onTaskCreated={onTaskCreated} />
          {loadError && <p className="form-error global-error">{loadError}</p>}
        </section>
        <section className="workspace">
          <div className="content-column">
            {currentTask ? <ProgressPanel task={currentTask} onCancel={onCancel} /> : (
              <section className="empty-state"><span>◎</span><h2>报告工作台</h2><p>提交一个视频任务后，这里会显示真实的处理进度与最终报告。</p></section>
            )}
            {report && <ReportView taskId={currentTask.task_id} report={report} />}
          </div>
          <History tasks={tasks} activeTaskId={currentTask?.task_id} onSelect={selectTask} />
        </section>
      </div>
    </main>
  )
}

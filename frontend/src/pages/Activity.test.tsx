import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { waitFor, screen, fireEvent, render, act } from '../test-utils'
import { ActivityPage } from './Activity'
import { useForgePipelineStore } from '../stores/useForgePipelineStore'

const {
  listRunsMock,
  getRunTasksMock,
  retryRunMock,
  navigateMock,
} = vi.hoisted(() => ({
  listRunsMock: vi.fn(),
  getRunTasksMock: vi.fn(),
  retryRunMock: vi.fn(),
  navigateMock: vi.fn(),
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => navigateMock,
  }
})

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>()
  return {
    ...actual,
    forgeFetchApi: {
      ...actual.forgeFetchApi,
      listRuns: (...args: unknown[]) => listRunsMock(...args),
      getRunTasks: (...args: unknown[]) => getRunTasksMock(...args),
      retryRun: (...args: unknown[]) => retryRunMock(...args),
    },
  }
})

describe('ActivityPage', () => {
  const toast = { success: vi.fn(), error: vi.fn() }

  beforeEach(() => {
    listRunsMock.mockReset()
    getRunTasksMock.mockReset()
    retryRunMock.mockReset()
    toast.success.mockReset()
    toast.error.mockReset()
    navigateMock.mockReset()
    useForgePipelineStore.getState().resetPipeline('fetch')
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('renders fetch runs and retries a failed task', async () => {
    listRunsMock.mockResolvedValue([
      {
        id: 'run-1',
        build_id: 'build-1',
        build_name: 'Build One',
        build_source: 'new_music',
        provider: 'tidarr',
        status: 'running',
        total_tasks: 1,
        processed_tasks: 0,
        active_tasks: 1,
        in_library: 0,
        failed: 0,
        unresolved: 0,
        stage_counts: { submitted: 1 },
        config: {},
        started_at: '2026-04-01T00:00:00',
        finished_at: null,
        created_at: '2026-04-01T00:00:00',
        updated_at: '2026-04-01T00:00:00',
      },
    ])
    getRunTasksMock.mockResolvedValue([
      {
        id: 10,
        run_id: 'run-1',
        build_id: 'build-1',
        provider: 'tidarr',
        artist_name: 'Artist A',
        album_name: 'Album A',
        stage: 'failed',
        metadata: {},
        retry_count: 0,
        created_at: '2026-04-01T00:00:00',
        updated_at: '2026-04-01T00:00:00',
        last_transition_at: '2026-04-01T00:00:00',
        error_message: 'network timeout',
      },
    ])
    retryRunMock.mockResolvedValue({
      status: 'ok',
      run_id: 'run-1',
      retried: 1,
      submission: { submitted: 1 },
      run: { id: 'run-1' },
    })

    render(<ActivityPage toast={toast} />)

    await waitFor(() => expect(screen.getByText('Build One')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('Artist A')).toBeInTheDocument())

    fireEvent.click(screen.getByText('Retry Task'))
    await waitFor(() => expect(retryRunMock).toHaveBeenCalledWith('run-1', [10]))
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
  })

  it('polls periodically for active fetch runs and shows websocket pipeline updates', async () => {
    listRunsMock.mockResolvedValue([
      {
        id: 'run-1',
        build_id: 'build-1',
        build_name: 'Build One',
        build_source: 'new_music',
        provider: 'tidarr',
        status: 'running',
        total_tasks: 2,
        processed_tasks: 1,
        active_tasks: 1,
        in_library: 0,
        failed: 0,
        unresolved: 0,
        stage_counts: { downloading: 1 },
        config: {},
        started_at: '2026-04-01T00:00:00',
        finished_at: null,
        created_at: '2026-04-01T00:00:00',
        updated_at: '2026-04-01T00:00:00',
      },
    ])
    getRunTasksMock.mockResolvedValue([])
    const setIntervalSpy = vi.spyOn(window, 'setInterval').mockImplementation((handler: TimerHandler) => {
      return 1 as unknown as number
    })
    vi.spyOn(window, 'clearInterval').mockImplementation(() => {})

    render(<ActivityPage toast={toast} />)
    await waitFor(() => expect(listRunsMock).toHaveBeenCalledTimes(1))
    expect(setIntervalSpy).toHaveBeenCalled()
    const pollIntervals = setIntervalSpy.mock.calls.map(call => call[1])
    expect(pollIntervals).toContain(10000)

    act(() => {
      useForgePipelineStore.getState().handleProgress({
        pipeline: 'fetch',
        run_id: 'run-1',
        stage: 'downloading',
        processed: 1,
        total: 2,
        message: 'Downloading',
      })
    })
    await waitFor(() => expect(useForgePipelineStore.getState().pipelines.fetch.running).toBe(true))
  })

  it('does not start interval polling when all runs are terminal', async () => {
    listRunsMock.mockResolvedValue([
      {
        id: 'run-2',
        build_id: 'build-2',
        build_name: 'Build Two',
        build_source: 'new_music',
        provider: 'tidarr',
        status: 'failed',
        total_tasks: 1,
        processed_tasks: 1,
        active_tasks: 0,
        in_library: 0,
        failed: 1,
        unresolved: 0,
        stage_counts: { failed: 1 },
        config: {},
        started_at: '2026-04-01T00:00:00',
        finished_at: '2026-04-01T00:01:00',
        created_at: '2026-04-01T00:00:00',
        updated_at: '2026-04-01T00:01:00',
      },
    ])
    getRunTasksMock.mockResolvedValue([])
    const setIntervalSpy = vi.spyOn(window, 'setInterval')

    render(<ActivityPage toast={toast} />)

    await waitFor(() => expect(screen.getByText('Build Two')).toBeInTheDocument())
    const pollIntervals = setIntervalSpy.mock.calls.map(call => call[1])
    expect(pollIntervals).not.toContain(10000)
  })

  it('uses slower polling for scan-waiting runs', async () => {
    listRunsMock.mockResolvedValue([
      {
        id: 'run-3',
        build_id: 'build-3',
        build_name: 'Build Three',
        build_source: 'new_music',
        provider: 'tidarr',
        status: 'running',
        total_tasks: 1,
        processed_tasks: 0,
        active_tasks: 1,
        in_library: 0,
        failed: 0,
        unresolved: 0,
        stage_counts: { scan_requested: 1 },
        config: {},
        started_at: '2026-04-01T00:00:00',
        finished_at: null,
        created_at: '2026-04-01T00:00:00',
        updated_at: '2026-04-01T00:00:00',
      },
    ])
    getRunTasksMock.mockResolvedValue([])
    const setIntervalSpy = vi.spyOn(window, 'setInterval')

    render(<ActivityPage toast={toast} />)

    await waitFor(() => expect(screen.getByText('Build Three')).toBeInTheDocument())
    const pollIntervals = setIntervalSpy.mock.calls.map(call => call[1])
    expect(pollIntervals).toContain(30000)
    expect(pollIntervals).not.toContain(10000)
  })
})

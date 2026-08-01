import { describe, expect, it } from 'vitest'
import {
  DOT_CLASS,
  TASK_STATUSES,
  orderTasks,
  taskCounts,
  type TaskSummary,
} from './tasks'

const task = (id: string, status: TaskSummary['status'], extra: Partial<TaskSummary> = {}): TaskSummary => ({
  id,
  subject: `task ${id}`,
  status,
  ...extra,
})

describe('task dock ordering', () => {
  it('keeps a blocked task in the dock', () => {
    // The regression this guards: three hard-coded filters concatenated
    // into `ordered` dropped every blocked step, so a plan whose last task
    // went blocked rendered an empty dock reading "0 active · 0 pending".
    const tasks = [task('1', 'completed'), task('2', 'blocked')]
    expect(orderTasks(tasks).map(t => t.id)).toEqual(['2', '1'])
  })

  it('puts active work first and finished work last', () => {
    const tasks = [
      task('1', 'completed'),
      task('2', 'pending'),
      task('3', 'blocked'),
      task('4', 'in_progress'),
    ]
    expect(orderTasks(tasks).map(t => t.id)).toEqual(['4', '3', '2', '1'])
  })

  it('never drops a task, whatever its status', () => {
    const tasks = TASK_STATUSES.map((status, index) => task(String(index), status))
    expect(orderTasks(tasks)).toHaveLength(TASK_STATUSES.length)
  })

  it('keeps a status this build does not know about', () => {
    // A server one version ahead must not make rows disappear.
    const future = { id: '9', subject: 'future', status: 'deferred' } as unknown as TaskSummary
    expect(orderTasks([task('1', 'pending'), future]).map(t => t.id)).toEqual(['1', '9'])
  })

  it('preserves relative order within a status', () => {
    const tasks = [task('1', 'pending'), task('2', 'pending'), task('3', 'pending')]
    expect(orderTasks(tasks).map(t => t.id)).toEqual(['1', '2', '3'])
  })
})

describe('task dock counts', () => {
  it('counts blocked separately from pending', () => {
    const counts = taskCounts([
      task('1', 'blocked'),
      task('2', 'blocked'),
      task('3', 'pending'),
    ])
    expect(counts).toEqual({ in_progress: 0, blocked: 2, pending: 1, completed: 0 })
  })

  it('reports zero for every status on an empty list', () => {
    expect(taskCounts([])).toEqual({
      in_progress: 0,
      blocked: 0,
      pending: 0,
      completed: 0,
    })
  })
})

describe('task dock palette', () => {
  it('has a dot class for every status', () => {
    for (const status of TASK_STATUSES) {
      expect(DOT_CLASS[status]).toBeTruthy()
    }
  })

  it('gives blocked its own colour, distinct from pending', () => {
    expect(DOT_CLASS.blocked).not.toBe(DOT_CLASS.pending)
  })
})

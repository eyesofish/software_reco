import { useCallback, useEffect, useRef } from 'react'

import { RecommendTaskStateResponse } from '~/entities/messages'
import { getTaskStateRequester } from '~/services/requester'

interface UseTaskPollingOptions {
  modelUrl : string,
  onTask : (task : RecommendTaskStateResponse) => void,
  onError ?: (error : unknown) => void,
  intervalMs ?: number
}

function sleep (ms : number, signal : AbortSignal) : Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve()
      return
    }

    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, ms)

    const onAbort = () => {
      clearTimeout(timer)
      signal.removeEventListener('abort', onAbort)
      resolve()
    }

    signal.addEventListener('abort', onAbort, { once: true })
  })
}

function isTerminalStatus (status : string) {
  const normalized = status.trim().toUpperCase()
  return normalized === 'DONE' || normalized === 'FAILED' || normalized === 'EXPIRED'
}

export default function useTaskPolling ({
  modelUrl,
  onTask,
  onError,
  intervalMs = 1500
} : UseTaskPollingOptions) {
  const controllerRef = useRef<AbortController|null>(null)
  const activeTaskIdRef = useRef<string|undefined>(undefined)

  const stopPolling = useCallback(() => {
    controllerRef.current?.abort()
    controllerRef.current = null
    activeTaskIdRef.current = undefined
  }, [])

  const startPolling = useCallback((taskId : string) => {
    const normalizedTaskId = taskId.trim()
    if (!normalizedTaskId) return

    stopPolling()
    const controller = new AbortController()
    controllerRef.current = controller
    activeTaskIdRef.current = normalizedTaskId

    void (async () => {
      while (!controller.signal.aborted) {
        try {
          const task = await getTaskStateRequester(modelUrl, normalizedTaskId, controller.signal)
          onTask(task)

          const status = String(task.status || '').trim().toUpperCase()
          if (isTerminalStatus(status)) {
            stopPolling()
            return
          }
        }
        catch (error) {
          if (controller.signal.aborted) {
            return
          }
          onError?.(error)
          stopPolling()
          return
        }

        await sleep(intervalMs, controller.signal)
      }
    })()
  }, [intervalMs, modelUrl, onError, onTask, stopPolling])

  useEffect(() => {
    return () => {
      stopPolling()
    }
  }, [stopPolling])

  return {
    startPolling,
    stopPolling,
    getActiveTaskId : () => activeTaskIdRef.current
  }
}
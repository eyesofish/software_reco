import { useEffect, useRef } from 'react'
import { useTheme } from 'styled-components'

import { I18N, useAppLanguage } from '~/services/language'
import '~/styles/splash.css'

interface Star {
  alpha: number
  radius: number
  speed: number
  x: number
  y: number
}

const STAR_COUNT = 220

function getStarSpeedMultiplier () {
  const savedStarSpeed = localStorage.getItem('starSpeed')
  if (savedStarSpeed === null) return 1

  const parsed = Number(savedStarSpeed)
  if (Number.isNaN(parsed)) return 1

  return Math.min(2, Math.max(0.1, parsed))
}

function hexToRgb (hex: string) {
  const trimmed = hex.replace('#', '')
  if (trimmed.length !== 6) return { r: 255, g: 255, b: 255 }
  const r = parseInt(trimmed.substring(0, 2), 16)
  const g = parseInt(trimmed.substring(2, 4), 16)
  const b = parseInt(trimmed.substring(4, 6), 16)
  if (Number.isNaN(r) || Number.isNaN(g) || Number.isNaN(b)) return { r: 255, g: 255, b: 255 }
  return { r, g, b }
}

export default function SplashScreen () {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const language = useAppLanguage()
  const theme = useTheme()

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const canvasElement = canvas

    const context = canvasElement.getContext('2d')
    if (!context) return
    const drawContext = context

    let frameId = 0
    let width = 0
    let height = 0
    let stars: Star[] = []
    const speedMultiplier = getStarSpeedMultiplier()
    const starRgb = hexToRgb(theme.colors.splashStar)

    function createStar (initialY ?: number) : Star {
      return {
        alpha: 0.2 + Math.random() * 0.7,
        radius: 0.4 + Math.random() * 1.8,
        speed: (0.15 + Math.random() * 0.65) * speedMultiplier,
        x: Math.random() * width,
        y: initialY ?? Math.random() * height
      }
    }

    function resetCanvas () {
      const pixelRatio = window.devicePixelRatio || 1
      width = window.innerWidth
      height = window.innerHeight
      canvasElement.width = Math.floor(width * pixelRatio)
      canvasElement.height = Math.floor(height * pixelRatio)
      canvasElement.style.width = `${width}px`
      canvasElement.style.height = `${height}px`
      drawContext.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0)
      stars = Array.from({ length: STAR_COUNT }, () => createStar())
    }

    function drawFrame () {
      drawContext.clearRect(0, 0, width, height)
      drawContext.fillStyle = theme.colors.splashCanvasBg
      drawContext.fillRect(0, 0, width, height)

      const now = performance.now() * 0.0015

      for (let index = 0; index < stars.length; index++) {
        const star = stars[index]
        star.y += star.speed
        if (star.y > height + star.radius) {
          stars[index] = createStar(-star.radius)
          continue
        }

        const twinkle = Math.sin(now + star.x * 0.03 + star.y * 0.02) * 0.2
        const alpha = Math.max(0.12, Math.min(1, star.alpha + twinkle))

        drawContext.beginPath()
        drawContext.fillStyle = `rgba(${starRgb.r}, ${starRgb.g}, ${starRgb.b}, ${alpha})`
        drawContext.arc(star.x, star.y, star.radius, 0, Math.PI * 2)
        drawContext.fill()
      }

      frameId = window.requestAnimationFrame(drawFrame)
    }

    resetCanvas()
    drawFrame()
    window.addEventListener('resize', resetCanvas)

    return () => {
      window.cancelAnimationFrame(frameId)
      window.removeEventListener('resize', resetCanvas)
    }
  }, [theme])

  return (
    <div className='splash-container'>
      <canvas id='starfield' ref={canvasRef}></canvas>
      <div className='splash-text'>{I18N[language].splashTitle}</div>
    </div>
  )
}

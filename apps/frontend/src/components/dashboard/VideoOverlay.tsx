/**
 * Floating draggable/resizable video overlay with crop mode and clone support.
 * Supports three display modes: floating (draggable), docked (above transcript), fullscreen.
 * Renders a hidden <video> and paints cropped frames to a visible <canvas>.
 */
import { useEffect, useRef, useState, useCallback } from 'react';

export interface CropRegion {
  x: number; y: number; w: number; h: number; // all 0–1 fractions of video natural dimensions
}

export type OverlayMode = 'floating' | 'docked' | 'fullscreen';

interface VideoOverlayProps {
  stream: MediaStream;
  cropRegion: CropRegion | null;
  onCropChange: (region: CropRegion | null) => void;
  onClone: () => void;
  onClose: () => void;
  mode: OverlayMode;
  onModeChange: (mode: OverlayMode) => void;
}

export function VideoOverlay({ stream, cropRegion, onCropChange, onClone, onClose, mode, onModeChange }: VideoOverlayProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number>(0);

  // Stagger initial position with a small random offset so clones don't stack exactly
  const [pos, setPos] = useState(() => ({
    x: window.innerWidth - 340 + Math.round(Math.random() * 40 - 20),
    y: window.innerHeight - 280 + Math.round(Math.random() * 40 - 20),
  }));
  const [size, setSize] = useState({ w: 320, h: 240 });

  const dragging = useRef(false);
  const resizing = useRef(false);
  const offset = useRef({ x: 0, y: 0 });

  // Docked-mode vertical resize state
  const [dockedHeight, setDockedHeight] = useState(360);
  const dockResizing = useRef(false);
  const dockResizeStart = useRef({ y: 0, h: 0 });

  // Crop mode state
  const [cropMode, setCropMode] = useState(false);
  const [cropDraft, setCropDraft] = useState<{ startX: number; startY: number; endX: number; endY: number } | null>(null);
  const cropDragging = useRef(false);

  // Attach stream to hidden video element
  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.srcObject = stream;
    }
  }, [stream]);

  // requestAnimationFrame render loop: draw cropped region from video to canvas
  useEffect(() => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;

    const draw = () => {
      const ctx = canvas.getContext('2d');
      if (!ctx || video.readyState < 2) {
        rafRef.current = requestAnimationFrame(draw);
        return;
      }

      // Match canvas internal resolution to its display size
      const displayW = canvas.clientWidth;
      const displayH = canvas.clientHeight;
      if (canvas.width !== displayW) canvas.width = displayW;
      if (canvas.height !== displayH) canvas.height = displayH;

      const vw = video.videoWidth;
      const vh = video.videoHeight;

      if (cropRegion) {
        const sx = cropRegion.x * vw;
        const sy = cropRegion.y * vh;
        const sw = cropRegion.w * vw;
        const sh = cropRegion.h * vh;
        ctx.drawImage(video, sx, sy, sw, sh, 0, 0, displayW, displayH);
      } else {
        ctx.drawImage(video, 0, 0, displayW, displayH);
      }

      rafRef.current = requestAnimationFrame(draw);
    };

    rafRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(rafRef.current);
  }, [stream, cropRegion]);

  // Global pointer move/up handlers for drag, resize, and dock resize
  const handlePointerMove = useCallback((e: PointerEvent) => {
    if (dragging.current) {
      setPos({ x: e.clientX - offset.current.x, y: e.clientY - offset.current.y });
    } else if (resizing.current) {
      setSize((prev) => ({
        w: Math.max(200, e.clientX - pos.x),
        h: Math.max(150, e.clientY - pos.y),
      }));
    } else if (dockResizing.current) {
      const delta = e.clientY - dockResizeStart.current.y;
      setDockedHeight(Math.max(80, dockResizeStart.current.h + delta));
    }
  }, [pos.x, pos.y]);

  const handlePointerUp = useCallback(() => {
    dragging.current = false;
    resizing.current = false;
    dockResizing.current = false;
  }, []);

  useEffect(() => {
    document.addEventListener('pointermove', handlePointerMove);
    document.addEventListener('pointerup', handlePointerUp);
    return () => {
      document.removeEventListener('pointermove', handlePointerMove);
      document.removeEventListener('pointerup', handlePointerUp);
    };
  }, [handlePointerMove, handlePointerUp]);

  const onHeaderPointerDown = (e: React.PointerEvent) => {
    if (mode !== 'floating') return;
    dragging.current = true;
    offset.current = { x: e.clientX - pos.x, y: e.clientY - pos.y };
  };

  const onResizePointerDown = (e: React.PointerEvent) => {
    if (mode !== 'floating') return;
    e.stopPropagation();
    resizing.current = true;
  };

  const onDockResizePointerDown = (e: React.PointerEvent) => {
    e.stopPropagation();
    dockResizing.current = true;
    dockResizeStart.current = { y: e.clientY, h: dockedHeight };
  };

  // ── Crop mode handlers ────────────────────────────────────────────

  const handleCropPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    setCropDraft({ startX: x, startY: y, endX: x, endY: y });
    cropDragging.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
  }, []);

  const handleCropPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!cropDragging.current) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));
    setCropDraft(prev => prev ? { ...prev, endX: x, endY: y } : null);
  }, []);

  const handleCropPointerUp = useCallback(() => {
    cropDragging.current = false;
    if (!cropDraft) return;

    const left = Math.min(cropDraft.startX, cropDraft.endX);
    const top = Math.min(cropDraft.startY, cropDraft.endY);
    const width = Math.abs(cropDraft.endX - cropDraft.startX);
    const height = Math.abs(cropDraft.endY - cropDraft.startY);

    // Ignore tiny selections (< 5% in either dimension)
    if (width < 0.05 || height < 0.05) {
      setCropDraft(null);
      return;
    }

    // Compose new selection on top of existing crop
    let newRegion: CropRegion;
    if (cropRegion) {
      newRegion = {
        x: cropRegion.x + left * cropRegion.w,
        y: cropRegion.y + top * cropRegion.h,
        w: width * cropRegion.w,
        h: height * cropRegion.h,
      };
    } else {
      newRegion = { x: left, y: top, w: width, h: height };
    }

    onCropChange(newRegion);
    setCropDraft(null);
    setCropMode(false);
  }, [cropDraft, cropRegion, onCropChange]);

  // Compute crop selection visual rectangles
  const selectionStyle = cropDraft ? (() => {
    const left = Math.min(cropDraft.startX, cropDraft.endX);
    const top = Math.min(cropDraft.startY, cropDraft.endY);
    const width = Math.abs(cropDraft.endX - cropDraft.startX);
    const height = Math.abs(cropDraft.endY - cropDraft.startY);
    return { left: `${left * 100}%`, top: `${top * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` };
  })() : null;

  // Dim regions surrounding the selection
  const dimRegions = cropDraft ? (() => {
    const l = Math.min(cropDraft.startX, cropDraft.endX);
    const t = Math.min(cropDraft.startY, cropDraft.endY);
    const w = Math.abs(cropDraft.endX - cropDraft.startX);
    const h = Math.abs(cropDraft.endY - cropDraft.startY);
    return [
      { left: '0', top: '0', width: '100%', height: `${t * 100}%` },               // top
      { left: '0', top: `${(t + h) * 100}%`, width: '100%', height: `${(1 - t - h) * 100}%` }, // bottom
      { left: '0', top: `${t * 100}%`, width: `${l * 100}%`, height: `${h * 100}%` },          // left
      { left: `${(l + w) * 100}%`, top: `${t * 100}%`, width: `${(1 - l - w) * 100}%`, height: `${h * 100}%` }, // right
    ];
  })() : null;

  const wrapperStyle = mode === 'floating'
    ? { left: pos.x, top: pos.y, width: size.w, height: size.h }
    : mode === 'docked'
    ? { height: dockedHeight }
    : {};

  return (
    <div
      className={`video-overlay video-overlay--${mode}`}
      style={wrapperStyle}
    >
      <div className="video-overlay__header" onPointerDown={onHeaderPointerDown}>
        <span className="video-overlay__title">Meeting Video</span>
        <div className="video-overlay__controls">
          <button
            className={`video-overlay__btn${cropMode ? ' video-overlay__btn--active' : ''}`}
            onClick={() => { setCropMode(m => !m); setCropDraft(null); }}
            aria-label="Crop video"
            title="Crop"
          >
            ✂
          </button>
          {cropRegion && (
            <button
              className="video-overlay__btn"
              onClick={() => onCropChange(null)}
              aria-label="Reset crop"
              title="Reset"
            >
              ↺
            </button>
          )}
          {mode === 'floating' && (
            <button
              className="video-overlay__btn"
              onClick={() => onModeChange('docked')}
              aria-label="Dock overlay"
              title="Dock"
            >
              &#x1F4CC;
            </button>
          )}
          {mode === 'docked' && (
            <button
              className="video-overlay__btn"
              onClick={() => onModeChange('floating')}
              aria-label="Undock overlay"
              title="Undock"
            >
              ⬆
            </button>
          )}
          {mode !== 'fullscreen' && (
            <button
              className="video-overlay__btn"
              onClick={() => onModeChange('fullscreen')}
              aria-label="Fullscreen overlay"
              title="Fullscreen"
            >
              ⛶
            </button>
          )}
          {mode === 'fullscreen' && (
            <button
              className="video-overlay__btn"
              onClick={() => onModeChange('floating')}
              aria-label="Exit fullscreen"
              title="Exit fullscreen"
            >
              ⬆
            </button>
          )}
          <button
            className="video-overlay__btn"
            onClick={onClone}
            aria-label="Clone overlay"
            title="Clone"
          >
            +
          </button>
          <button
            className="video-overlay__btn video-overlay__btn--close"
            onClick={onClose}
            aria-label="Close video overlay"
            title="Close"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Hidden video element — source for canvas drawing */}
      <video
        ref={videoRef}
        style={{ position: 'absolute', width: 0, height: 0, opacity: 0, pointerEvents: 'none' }}
        autoPlay
        muted
        playsInline
      />

      <canvas ref={canvasRef} className="video-overlay__canvas" />

      {/* Crop mode overlay */}
      {cropMode && (
        <div
          className="video-overlay__crop-overlay"
          onPointerDown={handleCropPointerDown}
          onPointerMove={handleCropPointerMove}
          onPointerUp={handleCropPointerUp}
        >
          {dimRegions && dimRegions.map((style, i) => (
            <div key={i} className="video-overlay__crop-dim" style={style} />
          ))}
          {selectionStyle && (
            <div className="video-overlay__crop-selection" style={selectionStyle} />
          )}
        </div>
      )}

      <div
        className="video-overlay__resize-handle"
        onPointerDown={onResizePointerDown}
      />

      {/* Docked-mode bottom resize divider */}
      {mode === 'docked' && (
        <div
          className="video-overlay__dock-resize"
          onPointerDown={onDockResizePointerDown}
        />
      )}
    </div>
  );
}

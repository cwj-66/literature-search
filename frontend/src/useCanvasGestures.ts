import { useRef, type PointerEvent, type MouseEvent } from "react";
import { transformGesture, type CanvasCamera, type CanvasPoint } from "./canvasGestures";

export function useCanvasGestures(camera: CanvasCamera, setCamera: (camera: CanvasCamera) => void, options: {
  min: number; max: number; onInteract?: () => void; onDragging?: (dragging: boolean) => void; onBackgroundTap?: () => void;
}) {
  const current = useRef(camera); current.current = camera;
  const pointers = useRef(new Map<number, CanvasPoint>());
  const origin = useRef<CanvasPoint | null>(null);
  const moved = useRef(false);
  const controls = (target: EventTarget) => target instanceof Element && !!target.closest('button:not(.scholar-node):not(.space-node),a');
  const point = (event: PointerEvent<HTMLElement>) => { const r = event.currentTarget.getBoundingClientRect(); return { x: event.clientX - r.left, y: event.clientY - r.top }; };
  const finish = (event: PointerEvent<HTMLElement>, cancelled = false) => {
    if (!pointers.current.has(event.pointerId)) return;
    pointers.current.delete(event.pointerId);
    if (!pointers.current.size) {
      options.onDragging?.(false);
      if (!cancelled && !moved.current && !(event.target as Element).closest('.scholar-node,.space-node')) options.onBackgroundTap?.();
      origin.current = null;
    } else origin.current = [...pointers.current.values()][0];
  };
  return { moved, handlers: {
    onPointerDown: (event: PointerEvent<HTMLElement>) => {
      if (controls(event.target) || event.pointerType === 'mouse' && event.button !== 0) return;
      if (!pointers.current.size) { moved.current = false; origin.current = point(event); }
      pointers.current.set(event.pointerId, point(event));
      if (pointers.current.size >= 2) {
        moved.current = true; options.onInteract?.(); options.onDragging?.(true);
        for (const id of pointers.current.keys()) event.currentTarget.setPointerCapture(id);
      }
    },
    onPointerMove: (event: PointerEvent<HTMLElement>) => {
      if (!pointers.current.has(event.pointerId)) return;
      const before = [...pointers.current.values()];
      pointers.current.set(event.pointerId, point(event));
      const after = [...pointers.current.values()];
      if (!moved.current && pointers.current.size === 1 && origin.current && Math.hypot(after[0].x - origin.current.x, after[0].y - origin.current.y) < 5) return;
      moved.current = true; options.onInteract?.(); options.onDragging?.(true);
      event.currentTarget.setPointerCapture(event.pointerId);
      const next = transformGesture(current.current, before, after, options.min, options.max);
      current.current = next; setCamera(next);
    },
    onPointerUp: (event: PointerEvent<HTMLElement>) => finish(event),
    onPointerCancel: (event: PointerEvent<HTMLElement>) => finish(event, true),
    onLostPointerCapture: (event: PointerEvent<HTMLElement>) => finish(event, true),
    onClickCapture: (event: MouseEvent<HTMLElement>) => {
      if (moved.current && !controls(event.target)) { event.preventDefault(); event.stopPropagation(); }
    },
  } };
}

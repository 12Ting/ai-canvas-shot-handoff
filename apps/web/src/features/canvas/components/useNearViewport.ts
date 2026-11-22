import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useState,
  type RefCallback,
} from "react";

const NEAR_VIEWPORT_MARGIN_PX = 160;
const NEAR_VIEWPORT_ROOT_MARGIN = `${NEAR_VIEWPORT_MARGIN_PX}px`;
const CLIPPING_OVERFLOW = new Set(["auto", "clip", "hidden", "scroll"]);

type VisibilityListener = (isNearViewport: boolean) => void;

type ObserverGroup = {
  observer: IntersectionObserver;
  targets: Map<Element, Set<VisibilityListener>>;
};

type FallbackTarget = {
  listener: VisibilityListener;
  root: Element | null;
  visible: boolean | null;
};

const observerGroups = new Map<Element | null, ObserverGroup>();
const fallbackTargets = new Map<Element, FallbackTarget>();
let fallbackListenersActive = false;
let cancelScheduledFallback: (() => void) | null = null;

function visibilityRoot(element: Element): Element | null {
  return element.closest(".shot-track ol, .canvas-surface");
}

function observerGroup(root: Element | null): ObserverGroup {
  const existing = observerGroups.get(root);
  if (existing) return existing;
  const targets = new Map<Element, Set<VisibilityListener>>();
  const group: ObserverGroup = {
    observer: new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const listeners = targets.get(entry.target);
          if (!listeners) continue;
          for (const listener of listeners) listener(entry.isIntersecting);
        }
      },
      { root, rootMargin: NEAR_VIEWPORT_ROOT_MARGIN, threshold: 0 },
    ),
    targets,
  };
  observerGroups.set(root, group);
  return group;
}

function subscribeWithObserver(
  element: Element,
  listener: VisibilityListener,
): () => void {
  const root = visibilityRoot(element);
  const group = observerGroup(root);
  let listeners = group.targets.get(element);
  if (!listeners) {
    listeners = new Set();
    group.targets.set(element, listeners);
    group.observer.observe(element);
  }
  listeners.add(listener);
  return () => {
    listeners?.delete(listener);
    if (listeners?.size === 0) {
      group.targets.delete(element);
      group.observer.unobserve(element);
    }
    if (group.targets.size === 0) {
      group.observer.disconnect();
      observerGroups.delete(root);
    }
  };
}

function finiteRect(element: Element): DOMRect | null {
  const rect = element.getBoundingClientRect();
  if (
    !Number.isFinite(rect.left) ||
    !Number.isFinite(rect.right) ||
    !Number.isFinite(rect.top) ||
    !Number.isFinite(rect.bottom) ||
    rect.right <= rect.left ||
    rect.bottom <= rect.top
  ) {
    return null;
  }
  return rect;
}

function intersects(
  start: number,
  end: number,
  clipStart: number,
  clipEnd: number,
): boolean {
  return start < clipEnd && end > clipStart;
}

function clipsAxis(value: string): boolean {
  return CLIPPING_OVERFLOW.has(value.trim().toLowerCase());
}

function fallbackNearViewport(element: Element, root: Element | null): boolean {
  const target = finiteRect(element);
  if (!target) return false;
  const rootRect =
    root === null
      ? {
          left: 0,
          right: window.innerWidth,
          top: 0,
          bottom: window.innerHeight,
        }
      : finiteRect(root);
  if (!rootRect) return false;
  if (
    !intersects(
      target.left,
      target.right,
      rootRect.left - NEAR_VIEWPORT_MARGIN_PX,
      rootRect.right + NEAR_VIEWPORT_MARGIN_PX,
    ) ||
    !intersects(
      target.top,
      target.bottom,
      rootRect.top - NEAR_VIEWPORT_MARGIN_PX,
      rootRect.bottom + NEAR_VIEWPORT_MARGIN_PX,
    )
  ) {
    return false;
  }

  for (
    let ancestor = element.parentElement;
    ancestor !== null && ancestor !== root;
    ancestor = ancestor.parentElement
  ) {
    const style = getComputedStyle(ancestor);
    const clipsOverflow = clipsAxis(style.overflow);
    const clipsX = clipsOverflow || clipsAxis(style.overflowX);
    const clipsY = clipsOverflow || clipsAxis(style.overflowY);
    if (!clipsX && !clipsY) continue;
    const clip = finiteRect(ancestor);
    if (!clip) return false;
    if (
      (clipsX &&
        !intersects(target.left, target.right, clip.left, clip.right)) ||
      (clipsY &&
        !intersects(target.top, target.bottom, clip.top, clip.bottom))
    ) {
      return false;
    }
  }
  return true;
}

function measureFallbackTargets(): void {
  for (const [element, target] of fallbackTargets) {
    const visible = fallbackNearViewport(element, target.root);
    if (visible === target.visible) continue;
    target.visible = visible;
    target.listener(visible);
  }
}

function scheduleFallbackMeasure(): void {
  if (cancelScheduledFallback !== null) return;
  if (typeof requestAnimationFrame === "function") {
    let completedSynchronously = false;
    const frame = requestAnimationFrame(() => {
      completedSynchronously = true;
      cancelScheduledFallback = null;
      measureFallbackTargets();
    });
    if (!completedSynchronously) {
      cancelScheduledFallback = () => cancelAnimationFrame(frame);
    }
    return;
  }
  const timer = window.setTimeout(() => {
    cancelScheduledFallback = null;
    measureFallbackTargets();
  }, 16);
  cancelScheduledFallback = () => window.clearTimeout(timer);
}

function setFallbackListeners(active: boolean): void {
  if (active === fallbackListenersActive) return;
  fallbackListenersActive = active;
  const method = active ? "addEventListener" : "removeEventListener";
  window[method]("scroll", scheduleFallbackMeasure, true);
  window[method]("resize", scheduleFallbackMeasure);
  window[method]("wheel", scheduleFallbackMeasure, true);
  window[method]("pointermove", scheduleFallbackMeasure, true);
}

function subscribeWithFallback(
  element: Element,
  listener: VisibilityListener,
): () => void {
  const target: FallbackTarget = {
    listener,
    root: visibilityRoot(element),
    visible: null,
  };
  fallbackTargets.set(element, target);
  setFallbackListeners(true);
  const visible = fallbackNearViewport(element, target.root);
  target.visible = visible;
  listener(visible);
  return () => {
    fallbackTargets.delete(element);
    if (fallbackTargets.size !== 0) return;
    setFallbackListeners(false);
    cancelScheduledFallback?.();
    cancelScheduledFallback = null;
  };
}

function subscribeNearViewport(
  element: Element,
  listener: VisibilityListener,
): () => void {
  return typeof IntersectionObserver === "function"
    ? subscribeWithObserver(element, listener)
    : subscribeWithFallback(element, listener);
}

export function useNearViewport<T extends Element>(enabled = true): {
  isNearViewport: boolean;
  ref: RefCallback<T>;
} {
  const [element, setElement] = useState<T | null>(null);
  const [isNearViewport, setIsNearViewport] = useState(false);
  const ref = useCallback<RefCallback<T>>((next) => setElement(next), []);

  useLayoutEffect(() => {
    if (
      enabled &&
      element !== null &&
      typeof IntersectionObserver !== "function" &&
      fallbackTargets.has(element)
    ) {
      scheduleFallbackMeasure();
    }
  });

  useEffect(() => {
    if (!enabled || element === null) {
      setIsNearViewport(false);
      return;
    }
    return subscribeNearViewport(element, setIsNearViewport);
  }, [element, enabled]);

  return { isNearViewport: enabled && isNearViewport, ref };
}

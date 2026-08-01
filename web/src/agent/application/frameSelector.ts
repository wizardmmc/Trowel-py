/** 将高频 Agent store 更新合并到浏览器帧后再发布给组件。 */

import { useLayoutEffect, useRef, useState } from "react";

import { useAgentStore } from "./store";

type Equality<T> = (left: T, right: T) => boolean;

/** 按浏览器帧发布 store selector 的最新值，避免高频事件逐条触发消息树刷新。 */
export function useAgentStoreFrameSelector<T>(
  selector: (state: ReturnType<typeof useAgentStore.getState>) => T,
  equality: Equality<T> = Object.is,
): T {
  const selectorRef = useRef(selector);
  const equalityRef = useRef(equality);
  selectorRef.current = selector;
  equalityRef.current = equality;

  const [selected, setSelected] = useState(() =>
    selector(useAgentStore.getState()),
  );
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const frameRef = useRef<number | null>(null);

  useLayoutEffect(() => {
    const latest = selectorRef.current(useAgentStore.getState());
    if (!equalityRef.current(selectedRef.current, latest)) {
      selectedRef.current = latest;
      setSelected(latest);
    }
  }, [selector, equality]);

  useLayoutEffect(() => {
    const unsubscribe = useAgentStore.subscribe((state) => {
      const next = selectorRef.current(state);
      if (equalityRef.current(selectedRef.current, next)) return;
      if (frameRef.current !== null) return;
      frameRef.current = window.requestAnimationFrame(() => {
        frameRef.current = null;
        const latest = selectorRef.current(useAgentStore.getState());
        if (equalityRef.current(selectedRef.current, latest)) return;
        selectedRef.current = latest;
        setSelected(latest);
      });
    });
    return () => {
      unsubscribe();
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
    };
  }, []);

  return selected;
}

/** 连接卡片提取进度 SSE，并把阶段进度交给调用方。 */

type ProgressHandler = (event: { stage: string; progress: number; message: string }) => void;

export function connectSSE(
  url: string,
  onProgress: ProgressHandler,
  onError?: (error: Event) => void
): EventSource {
  const source = new EventSource(url);

  source.addEventListener("extraction-progress", (e) => {
    const data = JSON.parse(e.data);
    onProgress(data);
  });

  source.onerror = (e) => {
    if (onError) {
      onError(e);
    }
    source.close();
  };

  return source;
}

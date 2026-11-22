import { digestStream } from "./fileDigest";
self.onmessage = async ({ data }: { data: File }) => {
  try {
    self.postMessage({ sha256: await digestStream(data.stream()) });
  } catch (error) {
    self.postMessage({
      error: error instanceof Error ? error.message : "无法读取文件",
    });
  }
};

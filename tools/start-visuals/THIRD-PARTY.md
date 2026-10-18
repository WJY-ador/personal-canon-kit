# 제3자 구성요소 — tools/start-visuals

렌더 도구(`still/`)가 npm 으로 받아 **제작자 Mac 에서만** 쓴다. 서버·페이지에는 코드가 들어가지 않고, 렌더 결과 그림(`server/assets/start-bg*.webp`)만 들어간다.

| 패키지 | 버전 | 라이선스 | 쓰는 곳 |
|---|---|---|---|
| shaders | 4.0.4 | MIT — Copyright (c) 2026 Shader Effects Inc. | MeshGradient 렌더(`shaders/core`) |
| typegpu | 0.12.3 | MIT | shaders 의존(WebGPU) |
| tinyest | 0.3.4 | MIT | typegpu 의존 |
| typed-binary | 4.3.3 | MIT | typegpu 의존 |
| tsover-runtime | 0.0.7 | Apache-2.0 | shaders 의존 |
| esbuild | 0.25.10 | MIT | 렌더용 번들(임시 폴더) |

정확한 버전·무결성은 `still/package-lock.json` 이 정본이다. 전문은 `npm ci` 뒤 `still/node_modules/<패키지>/LICENSE`.

## shaders — MIT License

Copyright (c) 2026 Shader Effects Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

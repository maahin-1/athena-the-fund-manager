import { defineConfig } from 'vitest/config';

export default defineConfig({
  build: { outDir: 'dist', target: 'es2022', sourcemap: false },
  test: { environment: 'node', include: ['test/**/*.test.ts'] },
});

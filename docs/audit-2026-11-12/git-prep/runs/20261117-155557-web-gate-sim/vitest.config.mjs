export default {
  root: 'C:/Users/LEGION/ath-wp07/cand-sim/terminal/web',
  envDir: 'C:/Users/LEGION/ath-wp07/web-work-1/no-dotfiles',
  cacheDir: 'C:/Users/LEGION/ath-wp07/web-work-1/vite-cache',
  server: { fs: { allow: ['C:/Users/LEGION/ath-wp07/cand-sim/terminal/web', 'C:/Users/LEGION/ath-wp07/web-work-1/guard'] } },
  test: { environment: 'node', include: ['test/**/*.test.ts'], setupFiles: ['C:/Users/LEGION/ath-wp07/web-work-1/guard/guard.setup.v3.mjs'] },
};

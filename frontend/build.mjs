import { copyFileSync, mkdirSync } from 'node:fs';
mkdirSync(new URL('./dist/', import.meta.url), { recursive: true });
copyFileSync(new URL('./src/styles.css', import.meta.url), new URL('./dist/styles.css', import.meta.url));
copyFileSync(new URL("./src/favicon.svg", import.meta.url), new URL("./dist/favicon.svg", import.meta.url));
console.log('Frontend built. No browser runtime dependencies or CDN requests.');

// Renders the boot animation's frames (docs/boot.md#boot-animation):
//   node assets/boot-anim/render.js FRAMES_DIR [FPS]
// writes FRAMES_DIR/f0000.png on, 750x640, one per frame at FPS (default 30) up to anim.html's TOTAL,
// each the average of 16 renders over a 45 ms shutter (anim.html renderBlur), without the corner mask
// the page shows in a browser. Needs `npm install playwright` and its Chromium, or CHROME naming one;
// anim.html loads the Outfit font from Google Fonts. tools/splash.py packs the frames.
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
(async () => {
  const [out, fps = '30'] = process.argv.slice(2);
  if (!out) throw new Error('usage: render.js FRAMES_DIR [FPS]');
  // A canvas stroke can hang headless Chromium's GPU process (seen under WSL): draw on the CPU.
  const browser = await chromium.launch({ args: ['--disable-gpu', '--disable-software-rasterizer'], executablePath: process.env.CHROME || undefined });
  const page = await browser.newPage();
  page.on('pageerror', e => { throw e; });
  await page.goto('file://' + path.join(__dirname, 'anim.html') + '?capture&scale=2&mask=0');
  await page.evaluate(() => window.ready);
  fs.mkdirSync(out, { recursive: true });
  const frames = Math.floor(await page.evaluate(() => TOTAL) * +fps);
  for (let i = 0; i <= frames; i++) {
    const url = await page.evaluate(t => { renderBlur(t, 0.045, 16); return document.getElementById('c').toDataURL('image/png'); }, i / +fps);
    fs.writeFileSync(path.join(out, 'f' + String(i).padStart(4, '0') + '.png'), Buffer.from(url.split(',')[1], 'base64'));
  }
  await browser.close();
})();

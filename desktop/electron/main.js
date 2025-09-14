const { app, BrowserWindow, Menu, shell } = require('electron');

function createWindow() {
  const url = process.env.BACKEND_URL || 'http://127.0.0.1:8000';
  const isDev = process.env.DEV_MODE === 'true';

  // Command-line switches for Chromium
  app.commandLine.appendSwitch('disable-features', 'TranslateUI,site-per-process,OutOfBlinkCors');
  app.commandLine.appendSwitch('disable-site-isolation-trials');
  app.commandLine.appendSwitch('disable-http-cache');

  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      enableRemoteModule: false,
      sandbox: true
    }
  });

  if (!isDev) {
    // In production: remove menu completely
    Menu.setApplicationMenu(null);
  }

  // Security hardening: block external navigation + popups
  win.webContents.on('will-navigate', (event, navUrl) => {
    const backend = process.env.BACKEND_URL || url;
    if (navUrl.startsWith(backend)) return; // allow in-app navigation
    // Open everything else (e.g., Microsoft login) in the system browser
    event.preventDefault();
    if (navUrl.startsWith('http')) shell.openExternal(navUrl);
  });

  // Handle HTTP redirects to external domains (e.g., 302 -> login.live.com)
  win.webContents.on('will-redirect', (event, navUrl) => {
    const backend = process.env.BACKEND_URL || url;
    if (navUrl.startsWith(backend)) return; // keep internal redirects in-app
    event.preventDefault();
    if (navUrl.startsWith('http')) shell.openExternal(navUrl);
  });

  win.webContents.setWindowOpenHandler(({ url: targetUrl }) => {
    const backend = process.env.BACKEND_URL || url;
    if (targetUrl.startsWith(backend)) {
      return { action: 'allow' };
    }
    // Open external links in default browser
    if (targetUrl.startsWith('http')) shell.openExternal(targetUrl);
    return { action: 'deny' };
  });

  win.loadURL(url);
}

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

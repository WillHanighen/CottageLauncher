const { app, BrowserWindow, Menu } = require('electron');

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
    if (!navUrl.startsWith(process.env.BACKEND_URL)) {
      event.preventDefault();
    }
  });

  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));

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

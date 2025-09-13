const { app, BrowserWindow, Menu } = require('electron');

function createWindow() {
  const url = process.env.BACKEND_URL || 'http://127.0.0.1:8000';
  const isDev = process.env.DEV_MODE === 'true';

  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true
    }
  });

  if (!isDev) {
    // In production: remove menu completely
    Menu.setApplicationMenu(null);
  }
  // In dev: keep default menu so keyboard shortcut works
  
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

import { el } from '../panels/base';
import type { PyFile } from '../formula/pyind';

/** The confirmation before a Python file is switched on. It says plainly what that allows, and for which version of the file. `confirm` returns the server's
 *  reason when it refuses, or null when the file was switched on. */
export function switchOnDialog(parent: HTMLElement, file: PyFile, confirm: () => Promise<string | null>): HTMLElement {
  const overlay = el('div', 'lab-modal');
  const box = el('div', 'lab-dialog lab-switchon');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-label', `switch on ${file.file}`);
  box.append(el('h3', '', `Switch on ${file.file}?`));
  box.append(
    el('p', 'bad', 'This file is Python code. Switching it on lets it run on this computer, with your rights, whenever a chart uses it.'),
    el('p', '', 'It runs in a separate process that is stopped after 20 seconds, with none of this program\'s settings in its environment, but it is NOT sandboxed: it can read your files and use your network. Switch it on only if you wrote it or have read it.'),
    el('p', 'note', `You are switching on this exact version of the file (SHA-256 ${file.sha256?.slice(0, 16) ?? '?'}…, ${file.size.toLocaleString('en-IN')} bytes). If the file changes in any way it is switched off again.`),
  );
  const message = el('div', 'note bad');
  const ok = el('button', 'btn', 'Switch on');
  ok.type = 'button';
  ok.addEventListener('click', async () => {
    message.textContent = '';
    const refused = await confirm();
    if (refused) message.textContent = refused;
    else overlay.remove();
  });
  const cancel = el('button', 'btn', 'Cancel');
  cancel.type = 'button';
  cancel.addEventListener('click', () => overlay.remove());
  overlay.addEventListener('keydown', (e) => e.key === 'Escape' && overlay.remove());
  const buttons = el('div', 'lab-buttons');
  buttons.append(ok, cancel);
  box.append(message, buttons);
  overlay.append(box);
  parent.append(overlay);
  cancel.focus();
  return overlay;
}

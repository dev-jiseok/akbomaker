import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import AudioInputNotice from './components/AudioInputNotice';

describe('input channel preparation notice', () => {
  it('does not relabel legacy or ordinary jobs as changed', () => {
    expect(renderToStaticMarkup(<AudioInputNotice />)).toBe('');
    expect(renderToStaticMarkup(<AudioInputNotice info={{ method: 'preserve-upload-stereo-cancellation-v1',
      input_channels: 2, used_channel_fallback: false, selected_channel: null, diagnostic_status: 'checked' }} />)).toBe('');
  });
  it('explains actual fallback and its limitation without an accuracy claim', () => {
    const html = renderToStaticMarkup(<AudioInputNotice info={{ method: 'preserve-upload-stereo-cancellation-v1',
      input_channels: 2, used_channel_fallback: true, selected_channel: 0, diagnostic_status: 'checked' }} />);
    expect(html).toContain('role="status"');
    expect(html).toContain('상쇄');
    expect(html).toContain('반대 채널');
    expect(html).not.toContain('정확도가');
  });
});

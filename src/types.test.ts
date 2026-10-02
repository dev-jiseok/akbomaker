import { describe, expect, it } from 'vitest';
import { formatTime, isYoutubeUrl, validateFile } from './types';

describe('music input safeguards', () => {
  it('accepts media files but refuses empty, unsupported and oversized files', () => {
    expect(validateFile({ name: '연습곡.MP4', size: 100 })).toBeNull();
    expect(validateFile({ name: 'song.wav', size: 0 })).not.toBeNull();
    expect(validateFile({ name: 'song.exe', size: 100 })).not.toBeNull();
    expect(validateFile({ name: 'song.mp3', size: 201 * 1024 * 1024 })).not.toBeNull();
  });
  it('only accepts a single video on an exact YouTube host over HTTPS', () => {
    expect(isYoutubeUrl('https://youtu.be/abcdefghijk')).toBe(true);
    expect(isYoutubeUrl('https://www.youtube.com/watch?v=abcdefghijk&t=30')).toBe(true);
    expect(isYoutubeUrl('https://youtube.com/shorts/abcdefghijk')).toBe(true);
    for (const url of ['https://youtube.com.evil.test/watch?v=abcdefghijk', 'http://youtu.be/abcdefghijk', 'https://user:password@youtu.be/abcdefghijk', 'https://youtube.com/playlist?list=abcdefghijk', 'https://youtu.be/short', 'https://localhost/watch?v=abcdefghijk']) expect(isYoutubeUrl(url)).toBe(false);
  });
  it('formats track duration safely before metadata is loaded', () => {
    expect(formatTime(null)).toBe('0:00');
    expect(formatTime(Infinity)).toBe('0:00');
    expect(formatTime(125.3)).toBe('2:05');
  });
});

import type { Job } from '../types';

export default function AudioInputNotice({ info }: { info?: Job['audio_preprocessing'] }) {
  if (info?.used_channel_fallback !== true) return null;
  return <p className="score-warning" role="status">
    업로드 음원의 좌우 채널을 합치면 소리가 거의 상쇄되어 원본 한 채널로 준비했어요.
    분리·채보는 이 음원을 기준으로 진행해요. 반대 채널의 독립적인 연주는 빠질 수 있으니 준비된 음원을 먼저 들어주세요.
  </p>;
}

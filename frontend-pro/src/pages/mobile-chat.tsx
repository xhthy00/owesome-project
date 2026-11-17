import { WorkspaceGate } from '@/workspace';
import Chat from '@/pages/chat';

export default function MobileChat() {
  return <WorkspaceGate><div className="pro-mobile-chat"><Chat /></div></WorkspaceGate>;
}

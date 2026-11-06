import { useContext } from "react";
import DatasourcePicker from "@/components/chat/DatasourcePicker";
import { ChatContentContext } from "@/new-components/chat/context";

export default function ToolsBar() {
  const { datasourceId, setDatasourceId } = useContext(ChatContentContext);
  return <DatasourcePicker value={datasourceId} onChange={setDatasourceId} compact />;
}

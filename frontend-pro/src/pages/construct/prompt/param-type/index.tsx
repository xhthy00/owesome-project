import { useRouter } from "@/routing/router";
import SectionPlaceholder from "@/components/layout/SectionPlaceholder";

export default function ConstructPromptTypePage() {
  const router = useRouter();
  return (
    <SectionPlaceholder
      title={`Construct / Prompt / ${String(router.query.type ?? "")}`}
      description="Prompt type detail placeholder."
    />
  );
}

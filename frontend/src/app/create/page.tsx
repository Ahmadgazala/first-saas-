import type { Metadata } from "next";
import { CreateWorkspace } from "@/components/CreateWorkspace";

export const metadata: Metadata = { title: "Create Puzzle" };

export default function CreatePage() {
  return <CreateWorkspace />;
}

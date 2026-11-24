import { useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Approval } from "../api";
import { ApprovalCard } from "../components/ApprovalCard";
import { Empty, PageHeader, Segmented, SkeletonList } from "../components/ui";

type Tab = "waiting" | "decided";

export default function Approvals() {
  const [tab, setTab] = useState<Tab>("waiting");
  const { data, isLoading } = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api.get<Approval[]>("/api/approvals"),
    refetchInterval: 4000,
  });
  const waiting = data?.filter((a) => a.status === "pending") ?? [];
  const decided = data?.filter((a) => a.status !== "pending") ?? [];
  const list = tab === "waiting" ? waiting : decided;

  return (
    <>
      <PageHeader area="approvals" />
      <div className="toolbar">
        <Segmented<Tab>
          label="Which requests"
          value={tab}
          onChange={setTab}
          options={[
            { value: "waiting", label: "Waiting for you", count: waiting.length },
            { value: "decided", label: "Already decided", count: decided.length },
          ]}
        />
        <span className="grow" />
        <Link to="/settings/rules" className="btn btn-ghost btn-sm">Approval rules</Link>
      </div>
      {isLoading ? (
        <div className="panel"><SkeletonList rows={3} /></div>
      ) : list.length === 0 ? (
        <div className="panel">
          <Empty hue="approvals" icon={ShieldCheck} title={tab === "waiting" ? "Nothing is waiting for you" : "No decisions yet"}>
          </Empty>
        </div>
      ) : (
        <div className="approval-list">
          {list.map((a) => <ApprovalCard key={a.id} a={a} />)}
        </div>
      )}
    </>
  );
}

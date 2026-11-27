import {
	NodeConnectionTypes,
	type IDataObject,
	type INodeExecutionData,
	type INodeType,
	type INodeTypeDescription,
	type IPollFunctions,
} from 'n8n-workflow';

const FINISHED = ['SUCCEEDED', 'FAILED', 'REJECTED'];
const PAGE = 500;

type RunSummary = IDataObject & { run_id: string; status: string; updated_at: string };

export function matchesEvent(event: string, run: RunSummary): boolean {
	if (event === 'needsApproval') return run.status === 'WAITING_APPROVAL';
	if (event === 'finished') return FINISHED.includes(run.status);
	return true;
}

export class SopWorkbenchTrigger implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'S&OP Workbench Trigger',
		name: 'sopWorkbenchTrigger',
		icon: 'file:sopWorkbench.svg',
		group: ['trigger'],
		version: 1,
		subtitle: '={{$parameter["event"]}}',
		description: 'Starts the workflow when a playbook run needs approval, finishes, or changes',
		defaults: { name: 'S&OP Workbench Trigger' },
		polling: true,
		inputs: [],
		outputs: [NodeConnectionTypes.Main],
		credentials: [{ name: 'sopWorkbenchApi', required: true }],
		properties: [
			{
				displayName: 'Event',
				name: 'event',
				type: 'options',
				options: [
					{ name: 'Run Needs Approval', value: 'needsApproval', description: 'A step is waiting for a human decision' },
					{ name: 'Run Finished', value: 'finished', description: 'Succeeded, failed or was rejected' },
					{ name: 'Any Run Change', value: 'anyChange' },
				],
				default: 'needsApproval',
			},
			{
				displayName: 'Playbook ID',
				name: 'playbookId',
				type: 'string',
				default: '',
				description: 'Only runs of this playbook; leave empty for all playbooks',
			},
		],
	};

	async poll(this: IPollFunctions): Promise<INodeExecutionData[][] | null> {
		const event = this.getNodeParameter('event') as string;
		const playbookId = (this.getNodeParameter('playbookId', '') as string).trim();
		const manual = this.getMode() === 'manual';
		const state = this.getWorkflowStaticData('node');

		// First activation: start from "now" so old runs are not replayed.
		if (!manual && typeof state.lastUpdatedAt !== 'string') {
			state.lastUpdatedAt = new Date().toISOString();
			return null;
		}

		const credentials = await this.getCredentials('sopWorkbenchApi');
		const baseUrl = String(credentials.baseUrl).replace(/\/+$/, '');
		const qs: IDataObject = { limit: manual ? 5 : PAGE };
		if (!manual) qs.updated_after = state.lastUpdatedAt;
		if (event === 'needsApproval') qs.status = 'WAITING_APPROVAL';
		if (playbookId) qs.workflow_id = playbookId;

		const response = (await this.helpers.httpRequestWithAuthentication.call(this, 'sopWorkbenchApi', {
			method: 'GET',
			url: `${baseUrl}/api/v1/workflow-runs`,
			qs,
			json: true,
		})) as { runs?: RunSummary[] };
		const runs = response.runs ?? [];

		// Changes come oldest-first, so the cursor advances even when more than one page is waiting.
		if (!manual && runs.length) state.lastUpdatedAt = runs[runs.length - 1].updated_at;

		const matching = runs.filter((r) => matchesEvent(event, r));
		if (!matching.length) return null;
		return [this.helpers.returnJsonArray(matching)];
	}
}

'use client';

import type { GraphStep } from '@/types/chat';
import { Icon, IconName } from '@/components/icons';

type StepIndicatorProps = {
	steps: GraphStep[];
	visible: boolean;
};

const Spinner = () => (
	<div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zinc-300 border-t-emerald-500 dark:border-zinc-600 dark:border-t-emerald-400" />
);

export const StepIndicator = ({ steps, visible }: StepIndicatorProps) => {
	if (!visible || steps.length === 0) return null;

	return (
		<div className="flex justify-start px-4">
			<div className="max-w-[80%] space-y-1.5 rounded-2xl bg-zinc-50 px-4 py-3 dark:bg-zinc-800/60">
				{steps.map((step, i) => (
					<div key={`${step.node}-${i}`} className="flex items-center gap-2">
						{step.status === 'completed' ? (
							<Icon name={IconName.Check} className="h-3.5 w-3.5 text-emerald-500" />
						) : (
							<Spinner />
						)}
						<span
							className={`text-xs ${
								step.status === 'active'
									? 'font-medium text-zinc-900 dark:text-zinc-100'
									: 'text-zinc-500 dark:text-zinc-400'
							}`}
						>
							{step.label}
						</span>
					</div>
				))}
			</div>
		</div>
	);
};

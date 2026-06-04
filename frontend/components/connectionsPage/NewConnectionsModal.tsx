// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useCallback, useMemo, useState } from 'react';
import { ModalWithSteps, type StepperFooterAction } from '@/components/ModalWithSteps';
import { ConnectionConnectStep } from '@/components/connectionsPage/steps/ConnectionConnectStep';
import { ConnectionSelectDataStep } from '@/components/connectionsPage/steps/ConnectionSelectDataStep';
import { ConnectionTypeStep } from '@/components/connectionsPage/steps/ConnectionTypeStep';
import { CONNECTION_TYPES_WITHOUT_SELECT_DATA, ConnectionType } from '@/enums/connection';
import type { ConnectionDraft } from '@/types/connectionDraft';

const NEW_CONNECTION_STEPS = ['Select Connector', 'Connect', 'Select Data'] as const;

export type NewConnectionsModalProps = {
	open: boolean;
	onConfirm: () => void;
	onCancel: () => void;
	connectionId?: string;
};

const emptyDraft = (type: ConnectionType = ConnectionType.POSTGRESQL): ConnectionDraft => ({
	type,
	name: '',
	description: '',
	connectionString: '',
	databases: [],
});

const isConnectStepValid = (draft: ConnectionDraft): boolean =>
	draft.name.trim().length > 0 && draft.connectionString.trim().length > 0;

export const NewConnectionsModal = ({
	open,
	onConfirm,
	onCancel,
	connectionId,
}: NewConnectionsModalProps) => {
	const [loading, setLoading] = useState(false);
	const [activeStep, setActiveStep] = useState(connectionId ? 1 : 0);
	const [connectionType, setConnectionType] = useState<ConnectionType>(ConnectionType.POSTGRESQL);
	const [draft, setDraft] = useState<ConnectionDraft>(emptyDraft());
	const [availableDatabases, setAvailableDatabases] = useState<string[]>([]);
	const [alert, setAlert] = useState<string | null>(null);

	const skipSelectDataStep = CONNECTION_TYPES_WITHOUT_SELECT_DATA.includes(connectionType);

	const steps = useMemo(() => {
		if (activeStep === 0 && !connectionId) {
			return [...NEW_CONNECTION_STEPS];
		}
		return skipSelectDataStep ? NEW_CONNECTION_STEPS.slice(0, -1) : [...NEW_CONNECTION_STEPS];
	}, [activeStep, connectionId, skipSelectDataStep]);

	const canContinue = useMemo(() => {
		if (activeStep === 0) return false;
		if (activeStep === 1) return isConnectStepValid(draft);
		if (activeStep === steps.length - 1 && !skipSelectDataStep) {
			return availableDatabases.length === 0 || draft.databases.length > 0;
		}
		return true;
	}, [activeStep, draft, steps.length, skipSelectDataStep, availableDatabases.length]);

	const handleNext = useCallback((): void => {
		setAlert(null);
		setActiveStep((prev) => Math.min(prev + 1, steps.length - 1));
	}, [steps.length]);

	const handleBack = useCallback((): void => {
		setAlert(null);
		setActiveStep((prev) => Math.max(prev - 1, connectionId ? 1 : 0));
	}, [connectionId]);

	const handleSelectType = (type: ConnectionType): void => {
		setConnectionType(type);
		setDraft(emptyDraft(type));
		setAvailableDatabases([]);
		setAlert(null);
		setActiveStep(1);
	};

	const handleDraftChange = (patch: Partial<ConnectionDraft>): void => {
		setDraft((prev) => ({ ...prev, ...patch }));
	};

	const handleCreate = useCallback(async (): Promise<void> => {
		setLoading(true);
		setAlert(null);
		// TODO: connectionsApi.create(draft) when backend is ready
		await new Promise((resolve) => {
			window.setTimeout(resolve, 300);
		});
		setLoading(false);
		onConfirm();
	}, [onConfirm]);

	const handleEdit = useCallback(async (): Promise<void> => {
		if (!connectionId) return;
		setLoading(true);
		setAlert(null);
		// TODO: connectionsApi.update(connectionId, draft)
		await new Promise((resolve) => {
			window.setTimeout(resolve, 300);
		});
		setLoading(false);
		onConfirm();
	}, [connectionId, onConfirm]);

	const renderStepContent = (step: number) => {
		switch (step) {
			case 0:
				return <ConnectionTypeStep onSelect={handleSelectType} />;
			case 1:
				return (
					<ConnectionConnectStep
						draft={{ ...draft, type: connectionType }}
						onChange={handleDraftChange}
						loading={false}
					/>
				);
			case 2:
				return (
					<ConnectionSelectDataStep
						availableDatabases={availableDatabases}
						selectedDatabases={draft.databases}
						onSelectionChange={(databases) => handleDraftChange({ databases })}
					/>
				);
			default:
				return null;
		}
	};

	const footerActions: StepperFooterAction[] = useMemo(() => {
		if (activeStep === 0) {
			return [{ label: 'Cancel', onClick: onCancel, variant: 'outline' }];
		}

		const actions: StepperFooterAction[] = [];

		if (!connectionId) {
			actions.push({ label: 'Back', onClick: handleBack, variant: 'outline' });
		}

		const isLastStep = activeStep === steps.length - 1;
		if (isLastStep) {
			actions.push({
				label: connectionId ? 'Update' : 'Create',
				onClick: () => {
					void (connectionId ? handleEdit() : handleCreate());
				},
				disabled: !canContinue || loading,
				loading,
			});
		} else {
			actions.push({
				label: 'Next',
				onClick: handleNext,
				disabled: !canContinue || loading,
				loading,
			});
		}

		return actions;
	}, [
		activeStep,
		canContinue,
		connectionId,
		handleBack,
		handleCreate,
		handleEdit,
		handleNext,
		loading,
		onCancel,
		steps.length,
	]);

	const modalTitle = connectionId ? 'Edit Connection' : 'Create New Connection';

	return (
		<ModalWithSteps
			open={open}
			onClose={onCancel}
			title={modalTitle}
			steps={steps}
			activeStep={activeStep}
			onActiveStepChange={setActiveStep}
			disabledSteps={connectionId ? [0] : []}
			footerActions={footerActions}
			alert={alert}
		>
			{renderStepContent(activeStep)}
		</ModalWithSteps>
	);
};

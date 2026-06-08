// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { connectionsApi } from '@/api/connections';
import { parseConnectionDatabaseName } from '@/lib/parseConnectionDatabaseName';
import { ModalWithSteps, type StepperFooterAction } from '@/components/ModalWithSteps';
import { ConnectionConnectStep } from '@/components/connectionsPage/steps/ConnectionConnectStep';
import { ConnectionTypeStep } from '@/components/connectionsPage/steps/ConnectionTypeStep';
import { ConnectionType, isConnectionType } from '@/enums/connection';
import type { Connection } from '@/types/connection';
import type { ConnectionInput } from '@/types/connectionInput';

const NEW_CONNECTION_STEPS = ['Select Connector', 'Connect'] as const;

export type NewConnectionsModalProps = {
	open: boolean;
	existingConnections?: Connection[];
	onConfirm: () => void;
	onCancel: () => void;
};

const emptyConnectionInput = (
	type: ConnectionType = ConnectionType.POSTGRESQL,
): ConnectionInput => ({
	type,
	connectionString: '',
});

export const NewConnectionsModal = ({
	open,
	existingConnections = [],
	onConfirm,
	onCancel,
}: NewConnectionsModalProps) => {
	const [loading, setLoading] = useState(false);
	const [testingConnection, setTestingConnection] = useState(false);
	const [isConnectionTested, setIsConnectionTested] = useState(false);
	const [testSuccessMessage, setTestSuccessMessage] = useState<string | null>(null);
	const [activeStep, setActiveStep] = useState(0);
	const [connectionType, setConnectionType] = useState<ConnectionType>(ConnectionType.POSTGRESQL);
	const [connectionInput, setConnectionInput] = useState<ConnectionInput>(emptyConnectionInput());
	const [alert, setAlert] = useState<string | null>(null);

	const disabledTypes = useMemo(() => {
		const used = new Set<ConnectionType>();
		for (const connection of existingConnections) {
			if (isConnectionType(connection.type)) {
				used.add(connection.type);
			}
		}
		return [...used];
	}, [existingConnections]);

	const canContinue = useMemo(() => {
		if (activeStep === 0) return false;
		return parseConnectionDatabaseName(connectionInput.connectionString) !== null;
	}, [activeStep, connectionInput.connectionString]);

	useEffect(() => {
		if (!open) return;
		setActiveStep(0);
		setConnectionType(ConnectionType.POSTGRESQL);
		setConnectionInput(emptyConnectionInput());
		setAlert(null);
		setIsConnectionTested(false);
		setTestSuccessMessage(null);
		setTestingConnection(false);
	}, [open]);

	const handleNext = useCallback((): void => {
		setAlert(null);
		setActiveStep((prev) => Math.min(prev + 1, NEW_CONNECTION_STEPS.length - 1));
	}, []);

	const handleBack = useCallback((): void => {
		setAlert(null);
		setActiveStep((prev) => Math.max(prev - 1, 0));
	}, []);

	const handleSelectType = (type: ConnectionType): void => {
		setConnectionType(type);
		setConnectionInput(emptyConnectionInput(type));
		setAlert(null);
		setIsConnectionTested(false);
		setTestSuccessMessage(null);
		setActiveStep(1);
	};

	const handleConnectionInputChange = (patch: Partial<ConnectionInput>): void => {
		setConnectionInput((prev) => ({ ...prev, ...patch }));
		if (patch.connectionString !== undefined) {
			setIsConnectionTested(false);
			setTestSuccessMessage(null);
			setAlert(null);
		}
	};

	const handleTestConnection = useCallback(async (): Promise<void> => {
		setTestingConnection(true);
		setAlert(null);
		setTestSuccessMessage(null);

		const res = await connectionsApi.test({
			type: connectionType,
			connectionString: connectionInput.connectionString,
		});
		setTestingConnection(false);

		if ('error' in res && res.error === true) {
			setIsConnectionTested(false);
			setAlert(res.message ?? 'Connection test failed.');
			return;
		}

		if (!('data' in res)) {
			setIsConnectionTested(false);
			setAlert('Connection test failed.');
			return;
		}

		const schemaCount = res.data.reduce((total, item) => total + item.schemas.length, 0);
		setIsConnectionTested(true);
		setTestSuccessMessage(
			schemaCount > 0
				? `Connection successful. Found ${schemaCount} schema${schemaCount === 1 ? '' : 's'}.`
				: 'Connection successful.',
		);
	}, [connectionType, connectionInput.connectionString]);

	const handleCreate = useCallback(async (): Promise<void> => {
		setLoading(true);
		setAlert(null);
		const res = await connectionsApi.create({
			type: connectionType,
			connectionString: connectionInput.connectionString,
		});
		setLoading(false);

		if ('error' in res && res.error === true) {
			setAlert(res.message ?? 'Failed to create connection.');
			return;
		}

		onConfirm();
	}, [connectionType, connectionInput, onConfirm]);

	const renderStepContent = (step: number) => {
		switch (step) {
			case 0:
				return (
					<ConnectionTypeStep onSelect={handleSelectType} disabledTypes={disabledTypes} />
				);
			case 1:
				return (
					<ConnectionConnectStep
						connectionInput={{ ...connectionInput, type: connectionType }}
						onChange={handleConnectionInputChange}
						testSuccessMessage={testSuccessMessage}
						onTestConnection={() => {
							void handleTestConnection();
						}}
						testDisabled={!canContinue || loading}
						testingConnection={testingConnection}
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

		const actions: StepperFooterAction[] = [
			{ label: 'Back', onClick: handleBack, variant: 'outline' },
		];

		const isLastStep = activeStep === NEW_CONNECTION_STEPS.length - 1;
		if (isLastStep) {
			actions.push({
				label: 'Create',
				onClick: () => {
					void handleCreate();
				},
				disabled: !canContinue || loading || testingConnection || !isConnectionTested,
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
		handleBack,
		handleCreate,
		handleNext,
		isConnectionTested,
		loading,
		onCancel,
		testingConnection,
	]);

	return (
		<ModalWithSteps
			open={open}
			onClose={onCancel}
			title="Create New Connection"
			steps={[...NEW_CONNECTION_STEPS]}
			activeStep={activeStep}
			onActiveStepChange={setActiveStep}
			footerActions={footerActions}
			alert={alert}
		>
			{renderStepContent(activeStep)}
		</ModalWithSteps>
	);
};

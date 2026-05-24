{{/* Common helpers */}}

{{- define "gsf.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "gsf.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "gsf.labels" -}}
app.kubernetes.io/name: {{ include "gsf.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "gsf.componentLabels" -}}
{{ include "gsf.labels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "gsf.componentSelectorLabels" -}}
app.kubernetes.io/name: {{ include "gsf.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "gsf.secretName" -}}
{{ include "gsf.fullname" . }}-secrets
{{- end -}}
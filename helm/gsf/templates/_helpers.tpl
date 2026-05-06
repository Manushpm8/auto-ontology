{{/*
Expand the name of the chart.
*/}}
{{- define "gsf.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this
(by the DNS naming spec).
*/}}
{{- define "gsf.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "gsf.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Component-scoped fullname: <fullname>-<component>
*/}}
{{- define "gsf.componentFullname" -}}
{{- $top := index . 0 -}}
{{- $component := index . 1 -}}
{{- printf "%s-%s" (include "gsf.fullname" $top) $component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels (applied to every resource).
*/}}
{{- define "gsf.labels" -}}
helm.sh/chart: {{ include "gsf.chart" . }}
{{ include "gsf.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: gsf
{{- end -}}

{{/*
Selector labels (must be stable across upgrades).
*/}}
{{- define "gsf.selectorLabels" -}}
app.kubernetes.io/name: {{ include "gsf.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Component-aware labels: pass list (root, "backend"|"frontend").
*/}}
{{- define "gsf.componentLabels" -}}
{{- $top := index . 0 -}}
{{- $component := index . 1 -}}
{{ include "gsf.labels" $top }}
app.kubernetes.io/component: {{ $component }}
{{- end -}}

{{- define "gsf.componentSelectorLabels" -}}
{{- $top := index . 0 -}}
{{- $component := index . 1 -}}
{{ include "gsf.selectorLabels" $top }}
app.kubernetes.io/component: {{ $component }}
{{- end -}}

{{/*
Resolve the image reference for a component.
Usage: include "gsf.image" (list .Values.backend .Values.image)
*/}}
{{- define "gsf.image" -}}
{{- $component := index . 0 -}}
{{- $globalImage := index . 1 -}}
{{- $appVersion := index . 2 -}}
{{- $tag := default $appVersion $component.image.tag -}}
{{- printf "%s:%s" $component.image.repository $tag -}}
{{- end -}}

{{/*
Resolve the imagePullPolicy: per-component override falls back to global.
*/}}
{{- define "gsf.imagePullPolicy" -}}
{{- $component := index . 0 -}}
{{- $globalImage := index . 1 -}}
{{- default $globalImage.pullPolicy $component.image.pullPolicy -}}
{{- end -}}

{{/*
ServiceAccount name for a component.
Usage: include "gsf.serviceAccountName" (list . "backend" .Values.backend.serviceAccount)
*/}}
{{- define "gsf.serviceAccountName" -}}
{{- $top := index . 0 -}}
{{- $component := index . 1 -}}
{{- $sa := index . 2 -}}
{{- if $sa.create -}}
{{- default (include "gsf.componentFullname" (list $top $component)) $sa.name -}}
{{- else -}}
{{- default "default" $sa.name -}}
{{- end -}}
{{- end -}}

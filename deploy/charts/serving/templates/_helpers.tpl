{{- define "serving.labels" -}}
app.kubernetes.io/name: serving-api
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "serving.selectorLabels" -}}
app.kubernetes.io/name: serving-api
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "serving.image" -}}
{{- printf "%s:%s" (required "image.repository must be set to the fully-qualified serving ECR repository" .Values.image.repository) (required "image.tag must be set to an immutable Git-SHA tag" .Values.image.tag) -}}
{{- end -}}

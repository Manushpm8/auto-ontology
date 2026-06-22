# Deploying GSF on Kubernetes

## Deployment From NVStaging

1. Fetch the chart from NGC:

   ```bash
   helm fetch https://helm.ngc.nvidia.com/nvstaging/gsf/charts/gsf-0.0.1.tgz \
     --username='$oauthtoken' \
     --password=<API-KEY>
   ```

2. Create the nvcr.io image-pull secret:

   ```bash
   kubectl create secret docker-registry nvcr-creds \
     --docker-server=nvcr.io \
     --docker-username='$oauthtoken' \
     --docker-password=<API-KEY>
   ```

3. Attach the pull secret to the default ServiceAccount so pods inherit it:

   ```bash
   kubectl patch serviceaccount default \
     -p '{"imagePullSecrets":[{"name":"nvcr-creds"}]}'
   ```

4. Install the chart:

   ```bash
   helm install gsf gsf-0.0.1.tgz \
     --set nvidiaApiKey=<API-KEY> \
     --set neo4jPassword=<NEO4J-PASSWORD> \
     --set postgresPassword=<POSTGRES-PASSWORD> \
     --set connectionStrings=<CONNECTION-STRINGS>
   ```

5. Expose the UI:

   ```bash
   kubectl port-forward frontend 3000:3000
   ```
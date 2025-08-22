#!/bin/bash

echo "Cleaning up Argo CD installation..."

# Delete the namespace (removes all namespace-scoped resources)
echo "Deleting argocd namespace..."
kubectl delete namespace argocd

# Delete Argo CD CustomResourceDefinitions (cluster-scoped)
echo "Deleting Argo CD CRDs..."
kubectl delete crd applications.argoproj.io
kubectl delete crd applicationsets.argoproj.io
kubectl delete crd appprojects.argoproj.io

# Delete Argo CD ClusterRole and ClusterRoleBinding (cluster-scoped)
echo "Deleting Argo CD cluster roles..."
kubectl delete clusterrole argocd-application-controller
kubectl delete clusterrolebinding argocd-application-controller

echo "Argo CD cleanup completed!" 
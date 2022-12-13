from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.generics import CreateAPIView, ListAPIView

from orgs.mixins.api import OrgBulkModelViewSet
from rbac.permissions import RBACPermission

from common.mixins import RecordViewLogMixin
from common.permissions import UserConfirmation
from authentication.const import ConfirmType
from assets.models import Account
from assets.filters import AccountFilterSet
from assets.tasks import verify_accounts_connectivity
from assets import serializers

__all__ = ['AccountViewSet', 'AccountSecretsViewSet', 'AccountTaskCreateAPI', 'AccountHistoriesSecretAPI']


class AccountViewSet(OrgBulkModelViewSet):
    model = Account
    search_fields = ('username', 'asset__address', 'name')
    filterset_class = AccountFilterSet
    serializer_classes = {
        'default': serializers.AccountSerializer,
        'verify': serializers.AssetTaskSerializer
    }
    rbac_perms = {
        'verify': 'assets.test_account',
        'partial_update': 'assets.change_accountsecret',
        'su_from_accounts': 'assets.view_account',
    }

    @action(methods=['get'], detail=True, url_path='su-from-accounts')
    def su_from_accounts(self, request, *args, **kwargs):
        account = super().get_object()
        accounts = account.get_su_from_accounts()
        serializer = serializers.AccountSerializer(accounts, many=True)
        return Response(data=serializer.data)

    @action(methods=['post'], detail=True, url_path='verify')
    def verify_account(self, request, *args, **kwargs):
        account = super().get_object()
        account_ids = [account.id]
        asset_ids = [account.asset_id]
        task = verify_accounts_connectivity.delay(account_ids, asset_ids)
        return Response(data={'task': task.id})


class AccountSecretsViewSet(RecordViewLogMixin, AccountViewSet):
    """
    因为可能要导出所有账号，所以单独建立了一个 viewset
    """
    serializer_classes = {
        'default': serializers.AccountSecretSerializer,
    }
    http_method_names = ['get', 'options']
    # Todo: 记得打开
    # permission_classes = [RBACPermission, UserConfirmation.require(ConfirmType.MFA)]
    rbac_perms = {
        'list': 'assets.view_accountsecret',
        'retrieve': 'assets.view_accountsecret',
    }


class AccountHistoriesSecretAPI(RecordViewLogMixin, ListAPIView):
    model = Account.history.model
    serializer_class = serializers.AccountHistorySerializer
    http_method_names = ['get', 'options']
    # Todo: 记得打开
    # permission_classes = [RBACPermission, UserConfirmation.require(ConfirmType.MFA)]
    rbac_perms = {
        'list': 'assets.view_accountsecret',
    }

    def get_queryset(self):
        return self.model.objects.filter(id=self.kwargs.get('pk'))


class AccountTaskCreateAPI(CreateAPIView):
    serializer_class = serializers.AccountTaskSerializer
    search_fields = AccountViewSet.search_fields
    filterset_class = AccountViewSet.filterset_class

    def check_permissions(self, request):
        return request.user.has_perm('assets.test_assetconnectivity')

    def get_accounts(self):
        queryset = Account.objects.all()
        queryset = self.filter_queryset(queryset)
        return queryset

    def perform_create(self, serializer):
        accounts = self.get_accounts()
        account_ids = accounts.values_list('id', flat=True)
        asset_ids = [account.asset_id for account in accounts]
        task = verify_accounts_connectivity.delay(account_ids, asset_ids)
        data = getattr(serializer, '_data', {})
        data["task"] = task.id
        setattr(serializer, '_data', data)
        return task

    def get_exception_handler(self):
        def handler(e, context):
            return Response({"error": str(e)}, status=400)
        return handler
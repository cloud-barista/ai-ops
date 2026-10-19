const check=ok=>{if(!ok)throw Error('network_binding_unverified');};
export async function auditNetworkBinding({intent,binding,provider}){
 check(binding?.availability_zone===intent.availability_zone&&binding.connection_name==='aws-us-west-2');
 for(const field of ['vnet_id','subnet_id','security_group_id','ssh_key_id','csp_vpc_id','csp_subnet_id','csp_security_group_id','csp_key_name'])check(typeof binding[field]==='string'&&/^[A-Za-z0-9._+-]{1,120}$/.test(binding[field]));
 const subnet=await provider('ec2','describe-subnets',['--subnet-ids',binding.csp_subnet_id]);check(subnet.Subnets?.length===1&&subnet.Subnets[0].SubnetId===binding.csp_subnet_id&&subnet.Subnets[0].VpcId===binding.csp_vpc_id&&subnet.Subnets[0].AvailabilityZone===intent.availability_zone&&subnet.Subnets[0].State==='available');
 const sg=await provider('ec2','describe-security-groups',['--group-ids',binding.csp_security_group_id]);check(sg.SecurityGroups?.length===1&&sg.SecurityGroups[0].GroupId===binding.csp_security_group_id&&sg.SecurityGroups[0].VpcId===binding.csp_vpc_id);
 const key=await provider('ec2','describe-key-pairs',['--key-names',binding.csp_key_name]);check(key.KeyPairs?.length===1&&key.KeyPairs[0].KeyName===binding.csp_key_name);return binding;
}
